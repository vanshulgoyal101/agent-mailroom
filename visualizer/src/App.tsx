import { useState, useEffect, useRef } from 'react'
import './App.css'

interface StatePayload {
  registry: Record<string, {
    owner: string
    endpoint: string
    modelCapabilities: string
    ratePerTaskWei: number
    active: boolean
    stake: number
  }>
  channels: Record<string, {
    sender: string
    recipient: string
    deposit: number
    challengeExpiry: number
    challenged: boolean
    dispute: {
      taskHash: string
      expiry: number
      active: boolean
    }
  }>
  stakes: Record<string, number>
  time_offset: number
  logs: string[]
  is_running_swarm: boolean
  simulated_vouchers?: Record<string, number>
}

function App() {
  const [data, setData] = useState<StatePayload | null>(null)
  const [offline, setOffline] = useState(true)
  const [isTriggering, setIsTriggering] = useState(false)
  const terminalContainerRef = useRef<HTMLDivElement | null>(null)
  const prevLogsLengthRef = useRef<number>(0)

  // Navigation tab state: 'swarm' | 'registry' | 'playground'
  const [activeTab, setActiveTab] = useState<'swarm' | 'registry' | 'playground'>('swarm')

  // Interactive sandbox states
  const [playgroundAgent, setPlaygroundAgent] = useState<'alice' | 'broker' | 'developer' | 'auditor'>('developer')
  const [playgroundAmount, setPlaygroundAmount] = useState<string>('0.1')
  const [channelSender, setChannelSender] = useState<'alice' | 'broker'>('alice')
  const [channelRecipient, setChannelRecipient] = useState<'broker' | 'developer' | 'auditor'>('broker')
  const [channelAction, setChannelAction] = useState<string>('open-channel')
  
  const [interactiveLoading, setInteractiveLoading] = useState(false)
  const [feedbackMsg, setFeedbackMsg] = useState<{ type: 'success' | 'error', text: string } | null>(null)

  // Map agent addresses to coordinates, names and styles
  const layout: Record<string, { x: number; y: number; name: string; role: string; color: string }> = {
    "0x35da118ee4b6a8301881b4c8b7761025107c64c4": { 
      x: 70, 
      y: 170, 
      name: "Alice (Buyer)", 
      role: "Client / Purchaser", 
      color: "var(--accent-cyan)" 
    },
    "0x1752ff3bdd7e3bb40aada7aad612db4b081f83f0": { 
      x: 230, 
      y: 170, 
      name: "Broker Agent", 
      role: "Swarm Orchestrator", 
      color: "var(--accent-purple)" 
    },
    "0xd79396dc9b3b10cadfaa601485afd5cd4887c4d1": { 
      x: 410, 
      y: 85, 
      name: "Developer Agent", 
      role: "Sub-Agent (Refactor)", 
      color: "var(--accent-green)" 
    },
    "0xda8b38dd735f1cb2368cbe319ec26bc9f03e514c": { 
      x: 410, 
      y: 255, 
      name: "Auditor Agent", 
      role: "Sub-Agent (Audit)", 
      color: "var(--accent-orange)" 
    }
  }

  const formatEth = (wei: number) => {
    return (wei / 1e18).toFixed(4)
  }

  // Poll state from simulator node
  useEffect(() => {
    const fetchState = async () => {
      try {
        const response = await fetch("http://127.0.0.1:8545/api/state")
        const payload = await response.json()
        setData(payload)
        setOffline(false)
      } catch (err) {
        setOffline(true)
      }
    }
    
    fetchState()
    const timer = setInterval(fetchState, 1000)
    return () => clearInterval(timer)
  }, [])

  // Auto-scroll console logs inside the terminal container only
  useEffect(() => {
    const currentLength = data?.logs?.length || 0
    if (currentLength !== prevLogsLengthRef.current) {
      prevLogsLengthRef.current = currentLength
      if (terminalContainerRef.current) {
        terminalContainerRef.current.scrollTop = terminalContainerRef.current.scrollHeight
      }
    }
  }, [data?.logs])

  const handleTriggerSwarm = async () => {
    if (data?.is_running_swarm || isTriggering) return
    setIsTriggering(true)
    setFeedbackMsg(null)
    try {
      await fetch("http://127.0.0.1:8545/api/run-swarm", { method: 'POST' })
    } catch (err) {
      console.error(err)
    } finally {
      setIsTriggering(false)
    }
  }

  const handleTimeTravel = async (hours: number) => {
    const seconds = hours * 3600
    try {
      await fetch("http://127.0.0.1:8545", {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          jsonrpc: "2.0",
          method: "evm_increaseTime",
          params: [seconds],
          id: 1
        })
      })
      await fetch("http://127.0.0.1:8545", {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          jsonrpc: "2.0",
          method: "evm_mine",
          params: [],
          id: 2
        })
      })
    } catch (err) {
      console.error(err)
    }
  }

  const handleInteractiveAction = async (endpoint: string, payload: any) => {
    setInteractiveLoading(true)
    setFeedbackMsg(null)
    try {
      const response = await fetch(`http://127.0.0.1:8545/api/interactive/${endpoint}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      })
      const result = await response.json()
      if (result.status === 'success') {
        setFeedbackMsg({ type: 'success', text: `Action executed successfully!` })
      } else {
        setFeedbackMsg({ type: 'error', text: result.message || 'Action failed.' })
      }
    } catch (err: any) {
      setFeedbackMsg({ type: 'error', text: err.message || 'Network error connecting to sandbox.' })
    } finally {
      setInteractiveLoading(false)
    }
  }

  const handleResetSandbox = async () => {
    setInteractiveLoading(true)
    setFeedbackMsg(null)
    try {
      await fetch("http://127.0.0.1:8545/api/interactive/reset", { method: 'POST' })
      setFeedbackMsg({ type: 'success', text: 'Sandbox node state reset to clean genesis.' })
    } catch (err: any) {
      setFeedbackMsg({ type: 'error', text: err.message || 'Reset failed.' })
    } finally {
      setInteractiveLoading(false)
    }
  }

  // Calculate stats
  const registeredCount = data ? Object.values(data.registry).filter(a => a.active).length : 0
  const totalStakedWei = data ? Object.values(data.registry).reduce((sum, current) => sum + (current.stake || 0), 0) : 0
  const activeChannelsCount = data ? Object.values(data.channels).length : 0

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '40px 20px' }}>
      
      {/* Top Header Navigation */}
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '32px', borderBottom: '1px solid rgba(255,255,255,0.05)', paddingBottom: '20px' }}>
        <div>
          <h1 style={{ fontSize: '1.8rem', fontWeight: 800, background: 'linear-gradient(90deg, #fff 0%, var(--accent-purple) 100%)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', letterSpacing: '-0.5px' }}>
            AgentMailroom Sandbox Console
          </h1>
          <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginTop: '4px' }}>
            Cryptographic Identity & Micro-payment Billing Playground for AI Swarms
          </p>
        </div>
        
        {/* Connection status tag */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', background: offline ? 'rgba(239, 68, 68, 0.08)' : 'rgba(16, 185, 129, 0.08)', border: offline ? '1px solid rgba(239, 68, 68, 0.2)' : '1px solid rgba(16, 185, 129, 0.2)', padding: '6px 12px', borderRadius: '20px', fontSize: '0.75rem', fontWeight: 600, color: offline ? 'var(--accent-red)' : 'var(--accent-green)' }}>
          <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: offline ? 'var(--accent-red)' : 'var(--accent-green)', display: 'inline-block' }}></span>
          {offline ? 'LOCAL SIMULATOR OFFLINE' : 'EVM MOCK NODE ONLINE'}
        </div>
      </header>

      {offline ? (
        <div className="glass-card" style={{ padding: '60px 20px', textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
          <h2 style={{ fontSize: '1.4rem', color: '#fff', marginBottom: '12px' }}>Simulator Offline</h2>
          <p style={{ color: 'var(--text-dim)', fontSize: '0.9rem', maxWidth: '460px', marginBottom: '24px', lineHeight: 1.5 }}>
            To run the visualizer, start the local EVM blockchain sandbox provider server in your terminal:
          </p>
          <code style={{ background: '#07080c', color: 'var(--accent-cyan)', padding: '12px 20px', borderRadius: '8px', fontFamily: 'var(--font-mono)', fontSize: '0.85rem', border: '1px solid rgba(255,255,255,0.05)' }}>
            python3 sandbox_node.py
          </code>
        </div>
      ) : (
        <>
          {/* Main Navigation Tabs */}
          <nav style={{ display: 'flex', gap: '12px', marginBottom: '24px' }}>
            <button 
              onClick={() => { setActiveTab('swarm'); setFeedbackMsg(null); }}
              style={{
                background: activeTab === 'swarm' ? 'rgba(168, 85, 247, 0.15)' : '#11131c',
                border: activeTab === 'swarm' ? '1px solid var(--accent-purple)' : '1px solid rgba(255,255,255,0.05)',
                color: activeTab === 'swarm' ? '#fff' : 'var(--text-secondary)',
                padding: '12px 20px',
                borderRadius: '8px',
                cursor: 'pointer',
                fontWeight: 600,
                fontSize: '0.9rem',
                transition: 'all 0.2s'
              }}
            >
              Swarm Automation
            </button>
            <button 
              onClick={() => { setActiveTab('registry'); setFeedbackMsg(null); }}
              style={{
                background: activeTab === 'registry' ? 'rgba(168, 85, 247, 0.15)' : '#11131c',
                border: activeTab === 'registry' ? '1px solid var(--accent-purple)' : '1px solid rgba(255,255,255,0.05)',
                color: activeTab === 'registry' ? '#fff' : 'var(--text-secondary)',
                padding: '12px 20px',
                borderRadius: '8px',
                cursor: 'pointer',
                fontWeight: 600,
                fontSize: '0.9rem',
                transition: 'all 0.2s'
              }}
            >
              Agent Registry Manager
            </button>
            <button 
              onClick={() => { setActiveTab('playground'); setFeedbackMsg(null); }}
              style={{
                background: activeTab === 'playground' ? 'rgba(168, 85, 247, 0.15)' : '#11131c',
                border: activeTab === 'playground' ? '1px solid var(--accent-purple)' : '1px solid rgba(255,255,255,0.05)',
                color: activeTab === 'playground' ? '#fff' : 'var(--text-secondary)',
                padding: '12px 20px',
                borderRadius: '8px',
                cursor: 'pointer',
                fontWeight: 600,
                fontSize: '0.9rem',
                transition: 'all 0.2s'
              }}
            >
              Manual Tunnel Playground
            </button>
          </nav>

          {/* Overview Metrics Bar */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '20px', marginBottom: '32px' }}>
            <div className="glass-card" style={{ padding: '20px' }}>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '1px' }}>DID Registry</span>
              <h3 style={{ fontSize: '2rem', fontWeight: 700, margin: '8px 0 4px', color: '#fff' }}>
                {registeredCount} AGENTS
              </h3>
              <p style={{ fontSize: '0.8rem', color: 'var(--text-dim)' }}>Decentralized Active Profiles</p>
            </div>

            <div className="glass-card" style={{ padding: '20px' }}>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '1px' }}>Registry Stake</span>
              <h3 style={{ fontSize: '2rem', fontWeight: 700, margin: '8px 0 4px', color: '#fff' }}>
                {formatEth(totalStakedWei)} ETH
              </h3>
              <p style={{ fontSize: '0.8rem', color: 'var(--text-dim)' }}>Locked Reputation Security</p>
            </div>

            <div className="glass-card" style={{ padding: '20px' }}>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '1px' }}>State Tunnels</span>
              <h3 style={{ fontSize: '2rem', fontWeight: 700, margin: '8px 0 4px', color: '#fff' }}>
                {activeChannelsCount} OPEN
              </h3>
              <p style={{ fontSize: '0.8rem', color: 'var(--text-dim)' }}>Off-chain Micro-payment Tunnels</p>
            </div>

            <div className="glass-card" style={{ padding: '20px' }}>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '1px' }}>EVM Clock State</span>
              <h3 style={{ fontSize: '2rem', fontWeight: 700, margin: '8px 0 4px', color: '#fff' }}>
                {data ? `+ ${(data.time_offset / 3600).toFixed(1)} hrs` : '0h'}
              </h3>
              <p style={{ fontSize: '0.8rem', color: 'var(--text-dim)' }}>Simulated Block Time Travel</p>
            </div>
          </div>

          {/* Feedback Message Alert */}
          {feedbackMsg && (
            <div style={{
              padding: '12px 18px',
              borderRadius: '8px',
              fontSize: '0.85rem',
              background: feedbackMsg.type === 'success' ? 'rgba(16, 185, 129, 0.08)' : 'rgba(239, 68, 68, 0.08)',
              border: feedbackMsg.type === 'success' ? '1px solid rgba(16, 185, 129, 0.2)' : '1px solid rgba(239, 68, 68, 0.2)',
              color: feedbackMsg.type === 'success' ? 'var(--accent-green)' : 'var(--accent-red)',
              fontWeight: 600,
              marginBottom: '24px'
            }}>
              {feedbackMsg.text}
            </div>
          )}

          {/* Tab Views */}
          {activeTab === 'swarm' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '32px' }}>
              
              {/* Swarm visualizer map & settings */}
              <div style={{ display: 'grid', gridTemplateColumns: '7fr 5fr', gap: '32px' }}>
                {/* SVG Visualizer */}
                <div className="glass-card" style={{ padding: '24px', display: 'flex', flexDirection: 'column' }}>
                  <h3 style={{ fontSize: '1.1rem', fontWeight: 600, color: '#fff', marginBottom: '16px' }}>Network Node Topology Map</h3>
                  <div style={{ flexGrow: 1, minHeight: '340px', background: '#07080c', borderRadius: '12px', position: 'relative', border: '1px solid rgba(255,255,255,0.03)' }}>
                    <svg width="100%" height="100%" viewBox="0 0 500 340" style={{ display: 'block' }}>
                      {/* Connection lines */}
                      {data && Object.entries(data.channels).map(([cid, chan]) => {
                        const fromNode = layout[chan.sender.toLowerCase()]
                        const toNode = layout[chan.recipient.toLowerCase()]
                        if (!fromNode || !toNode) return null

                        const isDisputed = chan.dispute && chan.dispute.active
                        const isChallenged = chan.challenged

                        return (
                          <g key={cid}>
                            <line 
                              x1={fromNode.x} 
                              y1={fromNode.y} 
                              x2={toNode.x} 
                              y2={toNode.y} 
                              stroke={isDisputed ? 'var(--accent-red)' : (isChallenged ? 'var(--accent-orange)' : 'var(--accent-purple)')}
                              strokeWidth="2.5" 
                              strokeDasharray={isChallenged ? '5,5' : 'none'}
                              opacity="0.6"
                            />
                            {data.is_running_swarm && (
                              <circle r="4" fill="var(--accent-cyan)">
                                <animateMotion 
                                  path={`M ${fromNode.x} ${fromNode.y} L ${toNode.x} ${toNode.y}`} 
                                  dur="1.5s" 
                                  repeatCount="indefinite" 
                                />
                              </circle>
                            )}
                          </g>
                        )
                      })}

                      {/* Nodes */}
                      {Object.entries(layout).map(([addr, details]) => {
                        const regState = data?.registry[addr.toLowerCase()]
                        const isRegistered = !!regState
                        const isActive = regState?.active

                        let nodeState: 'idle' | 'communicating' | 'disputed' = 'idle'
                        if (data?.is_running_swarm) {
                          nodeState = 'communicating'
                        }
                        if (data) {
                          for (const chan of Object.values(data.channels)) {
                            if ((chan.sender.toLowerCase() === addr.toLowerCase() || chan.recipient.toLowerCase() === addr.toLowerCase()) && chan.dispute && chan.dispute.active) {
                              nodeState = 'disputed'
                              break
                            }
                          }
                        }

                        return (
                          <g key={addr} transform={`translate(${details.x}, ${details.y})`}>
                            <circle 
                              r="32" 
                              fill="none" 
                              stroke={nodeState === 'disputed' ? 'var(--accent-red)' : (nodeState === 'communicating' ? 'var(--accent-cyan)' : details.color)}
                              strokeWidth="1.5"
                              opacity="0.3"
                              style={{
                                animation: nodeState === 'communicating' ? 'ripple 1.5s infinite linear' : 'none'
                              }}
                            />
                            <circle 
                              r="24" 
                              fill={isRegistered && isActive ? '#141621' : '#1e1f29'} 
                              stroke={nodeState === 'disputed' ? 'var(--accent-red)' : (nodeState === 'communicating' ? 'var(--accent-cyan)' : details.color)}
                              strokeWidth="2"
                            />
                            <text 
                              textAnchor="middle" 
                              dy=".3em" 
                              fill="#fff" 
                              fontSize="13px" 
                              fontWeight="700"
                            >
                              {details.name.split(' ')[0]}
                            </text>
                            <text 
                              y="42" 
                              textAnchor="middle" 
                              fill="var(--text-primary)" 
                              fontSize="11px" 
                              fontWeight="600"
                            >
                              {details.name}
                            </text>
                            <text 
                              y="54" 
                              textAnchor="middle" 
                              fill="var(--text-dim)" 
                              fontSize="9px" 
                              fontFamily="var(--font-mono)"
                            >
                              {details.role}
                            </text>
                          </g>
                        )
                      })}
                    </svg>
                  </div>
                </div>

                {/* Right controls column */}
                <div className="glass-card" style={{ padding: '24px', display: 'flex', flexDirection: 'column' }}>
                  <h3 style={{ fontSize: '1.1rem', fontWeight: 600, color: '#fff', marginBottom: '16px' }}>Swarm Controller</h3>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '24px', flexGrow: 1 }}>
                    
                    <div>
                      <h4 style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '8px' }}>E2E Automation</h4>
                      <p style={{ fontSize: '0.8rem', color: 'var(--text-dim)', marginBottom: '12px', lineHeight: 1.4 }}>
                        Trigger the composite multi-agent swarm flow. Alice will request a code audit scan, the Broker will resolve quotes and coordinate Developers and Auditors over secure channels automatically.
                      </p>
                      <button 
                        className="btn-cyber glow-purple-active" 
                        onClick={handleTriggerSwarm}
                        disabled={data?.is_running_swarm || isTriggering}
                        style={{ width: '100%', justifyContent: 'center', height: '48px' }}
                      >
                        {data?.is_running_swarm ? 'Swarm Running...' : 'Trigger Swarm Execution'}
                      </button>
                    </div>

                    <div style={{ borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: '16px' }}>
                      <h4 style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginBottom: '8px' }}>EVM Clock Controls</h4>
                      <p style={{ fontSize: '0.8rem', color: 'var(--text-dim)', marginBottom: '12px', lineHeight: 1.4 }}>
                        Fast-forward the simulator block timestamps to test dispute challenge periods or claim refunds.
                      </p>
                      <div style={{ display: 'flex', gap: '10px' }}>
                        <button 
                          onClick={() => handleTimeTravel(1)}
                          style={{ flex: 1, background: '#1c1e2a', border: '1px solid rgba(255,255,255,0.08)', color: '#fff', padding: '10px', borderRadius: '6px', cursor: 'pointer', fontWeight: 600, fontSize: '0.8rem' }}
                        >
                          + 1 Hour
                        </button>
                        <button 
                          onClick={() => handleTimeTravel(24)}
                          style={{ flex: 1, background: '#1c1e2a', border: '1px solid rgba(255,255,255,0.08)', color: '#fff', padding: '10px', borderRadius: '6px', cursor: 'pointer', fontWeight: 600, fontSize: '0.8rem' }}
                        >
                          + 24 Hours
                        </button>
                      </div>
                    </div>

                  </div>
                </div>
              </div>

              {/* Logs terminal box */}
              <div className="glass-card" style={{ padding: '24px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                  <h3 style={{ fontSize: '1.1rem', fontWeight: 600, color: '#fff' }}>Transaction Execution Logs</h3>
                  <span style={{ fontSize: '0.75rem', fontFamily: 'var(--font-mono)', color: 'var(--text-dim)' }}>shell-mode / raw</span>
                </div>
                <div 
                  ref={terminalContainerRef}
                  style={{
                    height: '240px',
                    background: '#07080c',
                    borderRadius: '12px',
                    padding: '16px',
                    fontFamily: 'var(--font-mono)',
                    fontSize: '0.8rem',
                    overflowY: 'auto',
                    border: '1px solid rgba(255,255,255,0.03)'
                  }}
                >
                  {data?.logs.length === 0 ? (
                    <div style={{ color: 'var(--text-dim)', textAlign: 'center', paddingTop: '80px' }}>
                      &gt; Console idle. Trigger swarm flow to stream logs...
                    </div>
                  ) : (
                    data?.logs.map((logLine, idx) => (
                      <div key={idx} className="terminal-line" style={{ marginBottom: '6px', color: logLine.includes('Error') || logLine.includes('fail') ? 'var(--accent-red)' : (logLine.includes('settled') || logLine.includes('successful') ? 'var(--accent-green)' : 'var(--text-primary)'), lineHeight: 1.5 }}>
                        &gt; {logLine}
                      </div>
                    ))
                  )}
                </div>
              </div>

            </div>
          )}

          {activeTab === 'registry' && (
            <div style={{ display: 'grid', gridTemplateColumns: '7fr 5fr', gap: '32px' }}>
              {/* Registry Active Profiles List */}
              <div className="glass-card" style={{ padding: '24px' }}>
                <h3 style={{ fontSize: '1.1rem', fontWeight: 600, color: '#fff', marginBottom: '16px' }}>On-Chain DID Registrations</h3>
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                    <thead>
                      <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.08)', textAlign: 'left', color: 'var(--text-secondary)' }}>
                        <th style={{ padding: '10px 6px' }}>Agent Name</th>
                        <th style={{ padding: '10px 6px' }}>capabilities</th>
                        <th style={{ padding: '10px 6px' }}>Pricing (ETH)</th>
                        <th style={{ padding: '10px 6px' }}>Collateral (ETH)</th>
                        <th style={{ padding: '10px 6px' }}>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data && Object.keys(layout).map((addr) => {
                        const name = layout[addr]?.name || addr.slice(0, 8)
                        const val = data.registry[addr]
                        return (
                          <tr key={addr} style={{ borderBottom: '1px solid rgba(255,255,255,0.03)', color: val?.active ? 'var(--text-primary)' : 'var(--text-dim)' }}>
                            <td style={{ padding: '12px 6px', fontWeight: 600 }}>{name}</td>
                            <td style={{ padding: '12px 6px' }}>{val ? val.modelCapabilities : 'Not Registered'}</td>
                            <td style={{ padding: '12px 6px', fontFamily: 'var(--font-mono)' }}>{val ? formatEth(val.ratePerTaskWei) : '-'}</td>
                            <td style={{ padding: '12px 6px', fontFamily: 'var(--font-mono)' }}>{val ? formatEth(val.stake) : '0.0000'}</td>
                            <td style={{ padding: '12px 6px' }}>
                              <span style={{
                                padding: '3px 8px',
                                borderRadius: '12px',
                                fontSize: '0.7rem',
                                fontWeight: 700,
                                background: val?.active ? 'rgba(16, 185, 129, 0.1)' : 'rgba(239, 68, 68, 0.1)',
                                color: val?.active ? 'var(--accent-green)' : 'var(--accent-red)'
                              }}>
                                {val?.active ? 'ACTIVE' : 'INACTIVE'}
                              </span>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Stake settings */}
              <div className="glass-card" style={{ padding: '24px' }}>
                <h3 style={{ fontSize: '1.1rem', fontWeight: 600, color: '#fff', marginBottom: '16px' }}>Reputation Staking Portal</h3>
                <p style={{ fontSize: '0.8rem', color: 'var(--text-dim)', marginBottom: '20px', lineHeight: 1.4 }}>
                  Agents are required to lock a minimum of 0.1 ETH collateral in the Registry contract before they can register as active service providers. Lock or unlock collateral manually here.
                </p>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                  <div>
                    <label style={{ display: 'block', fontSize: '0.8rem', color: 'var(--text-secondary)', marginBottom: '6px' }}>Target Agent Node</label>
                    <select 
                      value={playgroundAgent} 
                      onChange={(e) => setPlaygroundAgent(e.target.value as any)}
                      style={{ width: '100%', background: '#141621', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '10px', borderRadius: '6px', fontSize: '0.85rem' }}
                    >
                      <option value="alice">Alice (Buyer)</option>
                      <option value="broker">Broker Agent</option>
                      <option value="developer">Developer Agent</option>
                      <option value="auditor">Auditor Agent</option>
                    </select>
                  </div>

                  <div>
                    <label style={{ display: 'block', fontSize: '0.8rem', color: 'var(--text-secondary)', marginBottom: '6px' }}>Stake Collateral Value (ETH)</label>
                    <input 
                      type="number" 
                      value={playgroundAmount} 
                      onChange={(e) => setPlaygroundAmount(e.target.value)}
                      placeholder="0.1"
                      style={{ width: '100%', background: '#141621', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '10px', borderRadius: '6px', fontSize: '0.85rem' }}
                    />
                  </div>

                  <div style={{ display: 'flex', gap: '12px', marginTop: '10px' }}>
                    <button 
                      disabled={interactiveLoading}
                      onClick={() => handleInteractiveAction('stake', { agent: playgroundAgent, amount: playgroundAmount })}
                      style={{ flex: 1, padding: '12px', borderRadius: '6px', border: 'none', background: 'var(--accent-purple)', color: '#fff', fontSize: '0.85rem', fontWeight: 600, cursor: 'pointer', display: 'flex', justifyContent: 'center' }}
                    >
                      Stake Reputation
                    </button>
                    <button 
                      disabled={interactiveLoading}
                      onClick={() => handleInteractiveAction('unstake', { agent: playgroundAgent })}
                      style={{ flex: 1, padding: '12px', borderRadius: '6px', border: '1px solid rgba(255,255,255,0.1)', background: '#1c1e2a', color: '#fff', fontSize: '0.85rem', fontWeight: 600, cursor: 'pointer', display: 'flex', justifyContent: 'center' }}
                    >
                      Unstake Stake
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {activeTab === 'playground' && (
            <div style={{ display: 'grid', gridTemplateColumns: '7fr 5fr', gap: '32px' }}>
              
              {/* Active Channels List */}
              <div className="glass-card" style={{ padding: '24px', display: 'flex', flexDirection: 'column' }}>
                <h3 style={{ fontSize: '1.1rem', fontWeight: 600, color: '#fff', marginBottom: '16px' }}>Active State Tunnels</h3>
                
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', flexGrow: 1 }}>
                  {activeChannelsCount === 0 ? (
                    <div style={{ flexGrow: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', color: 'var(--text-dim)', minHeight: '260px' }}>
                      <p style={{ fontSize: '0.9rem' }}>No open state channels found.</p>
                      <p style={{ fontSize: '0.8rem', marginTop: '4px' }}>Use the channel controller on the right to open one.</p>
                    </div>
                  ) : (
                    data && Object.entries(data.channels).map(([cid, chan]) => {
                      const senderName = layout[chan.sender.toLowerCase()]?.name.split(' ')[0] || chan.sender.slice(0, 6)
                      const recipientName = layout[chan.recipient.toLowerCase()]?.name.split(' ')[0] || chan.recipient.slice(0, 6)
                      
                      const isDisputed = chan.dispute && chan.dispute.active
                      const isChallenged = chan.challenged

                      return (
                        <div key={cid} style={{ border: '1px solid rgba(255,255,255,0.03)', padding: '16px', borderRadius: '10px', background: 'rgba(0,0,0,0.15)' }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '10px', fontSize: '0.85rem' }}>
                            <span style={{ fontWeight: 600 }}>{senderName} ➔ {recipientName}</span>
                            <span style={{ color: isDisputed ? 'var(--accent-red)' : (isChallenged ? 'var(--accent-orange)' : 'var(--accent-cyan)'), fontWeight: 600 }}>
                              {formatEth(chan.deposit)} ETH
                            </span>
                          </div>
                          
                          <div className="progress-meter" style={{ height: '8px', marginBottom: '8px' }}>
                            <div className="progress-meter-fill" style={{
                              width: '100%',
                              background: isDisputed ? 'var(--accent-red)' : (isChallenged ? 'var(--accent-orange)' : 'linear-gradient(90deg, var(--accent-cyan) 0%, var(--accent-purple) 100%)')
                            }}></div>
                          </div>

                          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: 'var(--text-dim)' }}>
                            <span>ID: {cid.slice(0, 10)}...</span>
                            {isDisputed ? (
                              <span style={{ color: 'var(--accent-red)', fontWeight: 600 }}>DISPUTED ACTIVE</span>
                            ) : isChallenged ? (
                              <span style={{ color: 'var(--accent-orange)', fontWeight: 600 }}>CHALLENGED</span>
                            ) : (
                              <span style={{ color: 'var(--accent-green)' }}>SECURE State-Tunnel</span>
                            )}
                          </div>

                          {data?.simulated_vouchers?.[cid] !== undefined && data.simulated_vouchers[cid] > 0 && (
                            <div style={{
                              marginTop: '8px',
                              padding: '6px 10px',
                              background: 'rgba(34, 211, 238, 0.08)',
                              border: '1px dashed rgba(34, 211, 238, 0.2)',
                              borderRadius: '6px',
                              fontSize: '0.75rem',
                              color: 'var(--accent-cyan)',
                              display: 'flex',
                              justifyContent: 'space-between'
                            }}>
                              <span>Pending Voucher (Off-chain):</span>
                              <span style={{ fontWeight: 700 }}>{data.simulated_vouchers[cid].toFixed(4)} ETH</span>
                            </div>
                          )}
                        </div>
                      )
                    })
                  )}
                </div>
              </div>

              {/* State Channel Controller */}
              <div className="glass-card" style={{ padding: '24px' }}>
                <h3 style={{ fontSize: '1.1rem', fontWeight: 600, color: '#fff', marginBottom: '16px' }}>State-Tunnel Playground</h3>
                <p style={{ fontSize: '0.8rem', color: 'var(--text-dim)', marginBottom: '20px', lineHeight: 1.4 }}>
                  Open off-chain channels, issue vouchers (micro-payments), claim redemptions on-chain, or manually trigger disputes and slashing payouts.
                </p>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', gap: '10px', alignItems: 'center' }}>
                    <div>
                      <label style={{ display: 'block', fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '4px' }}>Sender</label>
                      <select 
                        value={channelSender} 
                        onChange={(e) => setChannelSender(e.target.value as any)}
                        style={{ width: '100%', background: '#141621', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '8px', borderRadius: '6px', fontSize: '0.8rem' }}
                      >
                        <option value="alice">Alice</option>
                        <option value="broker">Broker</option>
                      </select>
                    </div>
                    <span style={{ fontSize: '0.8rem', color: 'var(--text-dim)', paddingTop: '16px' }}>➔</span>
                    <div>
                      <label style={{ display: 'block', fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '4px' }}>Recipient</label>
                      <select 
                        value={channelRecipient} 
                        onChange={(e) => setChannelRecipient(e.target.value as any)}
                        style={{ width: '100%', background: '#141621', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '8px', borderRadius: '6px', fontSize: '0.8rem' }}
                      >
                        <option value="broker">Broker</option>
                        <option value="developer">Developer</option>
                        <option value="auditor">Auditor</option>
                      </select>
                    </div>
                  </div>

                  <div>
                    <label style={{ display: 'block', fontSize: '0.8rem', color: 'var(--text-secondary)', marginBottom: '6px' }}>Select Action</label>
                    <select 
                      value={channelAction} 
                      onChange={(e) => setChannelAction(e.target.value)}
                      style={{ width: '100%', background: '#141621', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', padding: '10px', borderRadius: '6px', fontSize: '0.85rem' }}
                    >
                      <option value="open-channel">Open State-Channel (0.05 ETH)</option>
                      <option value="send-voucher">Sign Off-chain Voucher (+0.01 ETH)</option>
                      <option value="redeem-voucher">Redeem Voucher On-chain (Settle)</option>
                      <option value="dispute">Initiate Dispute (Freeze Escrow)</option>
                      <option value="slash">Claim Slash (Slash Bob's Stake)</option>
                      <option value="challenge">Initiate Challenge (Refund Period)</option>
                      <option value="refund">Claim Refund (Reclaim Deposit)</option>
                    </select>
                  </div>

                  <button 
                    disabled={interactiveLoading}
                    onClick={() => handleInteractiveAction(channelAction, { sender: channelSender, recipient: channelRecipient, amount: 0.01 })}
                    style={{ width: '100%', padding: '12px', borderRadius: '6px', border: 'none', background: 'var(--accent-cyan)', color: '#000', fontSize: '0.85rem', fontWeight: 700, cursor: 'pointer', marginTop: '10px' }}
                  >
                    {interactiveLoading ? 'Processing...' : 'Execute Channel Action'}
                  </button>

                  <div style={{ display: 'flex', gap: '12px', borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: '16px', marginTop: '10px' }}>
                    <button 
                      disabled={interactiveLoading}
                      onClick={handleResetSandbox}
                      style={{ flex: 1, padding: '10px', borderRadius: '6px', border: '1px solid rgba(239, 68, 68, 0.3)', background: 'rgba(239, 68, 68, 0.08)', color: 'var(--accent-red)', fontSize: '0.8rem', fontWeight: 600, cursor: 'pointer' }}
                    >
                      Reset Simulator
                    </button>
                    <button 
                      onClick={() => handleTimeTravel(1)}
                      style={{ width: '90px', padding: '10px', borderRadius: '6px', border: '1px solid rgba(255,255,255,0.08)', background: '#1c1e2a', color: '#fff', fontSize: '0.8rem', fontWeight: 600, cursor: 'pointer' }}
                    >
                      + 1 Hour
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}
        </>
      )}

    </div>
  )
}

export default App
