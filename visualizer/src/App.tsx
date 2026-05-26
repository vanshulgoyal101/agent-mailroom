import { useEffect, useState, useRef } from 'react'

interface AgentState {
  owner: string
  endpoint: string
  modelCapabilities: string[]
  ratePerTaskWei: number
  active: boolean
  stake: number
}

interface DisputeState {
  taskHash: string
  expiry: number
  active: boolean
}

interface ChannelState {
  sender: string
  recipient: string
  deposit: number
  challengeExpiry: number
  challenged: boolean
  dispute: DisputeState
}

interface StatePayload {
  registry: Record<string, AgentState>
  channels: Record<string, ChannelState>
  stakes: Record<string, number>
  time_offset: number
  logs: string[]
  is_running_swarm: boolean
}

function App() {
  const [data, setData] = useState<StatePayload | null>(null)
  const [offline, setOffline] = useState(true)
  const [isTriggering, setIsTriggering] = useState(false)
  const terminalContainerRef = useRef<HTMLDivElement | null>(null)
  const prevLogsLengthRef = useRef<number>(0)

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

  // Poll node state
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
          id: 1,
          method: "evm_increaseTime",
          params: [seconds]
        })
      })
      await fetch("http://127.0.0.1:8545", {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          jsonrpc: "2.0",
          id: 2,
          method: "evm_mine",
          params: []
        })
      })
    } catch (err) {
      console.error(err)
    }
  }

  // Calculate stats
  const registeredCount = data ? Object.keys(data.registry).length : 0
  const activeChannelsCount = data ? Object.keys(data.channels).length : 0
  const totalStakedWei = data ? Object.values(data.stakes).reduce((a, b) => a + b, 0) : 0

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '40px 20px' }}>
      
      {/* Header Banner */}
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '32px' }}>
        <div>
          <h1 style={{ fontSize: '2.5rem', fontWeight: 800, letterSpacing: '-1px', color: '#fff', marginBottom: '4px' }}>
            AGENT<span style={{ color: 'var(--accent-purple)' }}>MAILROOM</span>
          </h1>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.95rem' }}>
            Decentralized Swarm Economy & State Channel Monitor
          </p>
        </div>
        
        {/* Status Indicator */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{
            width: '10px',
            height: '10px',
            borderRadius: '50%',
            backgroundColor: offline ? 'var(--accent-red)' : 'var(--accent-green)',
            boxShadow: offline ? '0 0 10px var(--accent-red)' : '0 0 10px var(--accent-green)'
          }}></span>
          <span style={{ fontSize: '0.85rem', fontWeight: 600, color: offline ? 'var(--accent-red)' : 'var(--accent-green)', textTransform: 'uppercase' }}>
            {offline ? 'EVM Node Offline' : 'EVM Node Online'}
          </span>
        </div>
      </header>

      {offline ? (
        <div className="glass-card" style={{ padding: '60px', textAlign: 'center', marginBottom: '40px' }}>
          <h2 style={{ color: 'var(--accent-red)', marginBottom: '16px' }}>Simulator Offline</h2>
          <p style={{ color: 'var(--text-secondary)', maxWidth: '500px', margin: '0 auto 24px', lineHeight: 1.6 }}>
            The Web visualizer cannot reach the mock EVM sandbox node. Please ensure that you have launched the Python local simulator by running:
          </p>
          <code style={{ padding: '12px 24px', borderRadius: '8px', fontSize: '0.95rem', color: '#fff', background: '#1c1e2a' }}>
            python3 sandbox_node.py
          </code>
        </div>
      ) : (
        <>
          {/* Top Level Stats Panel */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '20px', marginBottom: '32px' }}>
            <div className="glass-card" style={{ padding: '20px' }}>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '1px' }}>Active swarms</span>
              <h3 style={{ fontSize: '2rem', fontWeight: 700, margin: '8px 0 4px', color: '#fff' }}>
                {data?.is_running_swarm ? '1 ACTIVE' : '0 IDLE'}
              </h3>
              <p style={{ fontSize: '0.8rem', color: 'var(--text-dim)' }}>Dynamic workflow coordination</p>
            </div>
            
            <div className="glass-card" style={{ padding: '20px' }}>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '1px' }}>DID Registry</span>
              <h3 style={{ fontSize: '2rem', fontWeight: 700, margin: '8px 0 4px', color: '#fff' }}>
                {registeredCount} AGENTS
              </h3>
              <p style={{ fontSize: '0.8rem', color: 'var(--text-dim)' }}>Decentralized Profile Registry</p>
            </div>

            <div className="glass-card" style={{ padding: '20px' }}>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '1px' }}>Locked Stake</span>
              <h3 style={{ fontSize: '2rem', fontWeight: 700, margin: '8px 0 4px', color: '#fff' }}>
                {formatEth(totalStakedWei)} ETH
              </h3>
              <p style={{ fontSize: '0.8rem', color: 'var(--text-dim)' }}>Reputation Registry Collateral</p>
            </div>

            <div className="glass-card" style={{ padding: '20px' }}>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '1px' }}>Channels</span>
              <h3 style={{ fontSize: '2rem', fontWeight: 700, margin: '8px 0 4px', color: '#fff' }}>
                {activeChannelsCount} OPEN
              </h3>
              <p style={{ fontSize: '0.8rem', color: 'var(--text-dim)' }}>Off-chain Micro-payment tunnels</p>
            </div>
          </div>

          {/* Swarm Visualizer & State Panel */}
          <div style={{ display: 'grid', gridTemplateColumns: '7fr 5fr', gap: '32px', marginBottom: '32px' }}>
            
            {/* Interactive Network Graph */}
            <div className="glass-card" style={{ padding: '24px', display: 'flex', flexDirection: 'column' }}>
              <h3 style={{ fontSize: '1.1rem', fontWeight: 600, color: '#fff', marginBottom: '16px' }}>Network Node Topology Map</h3>
              
              <div style={{ flexGrow: 1, minHeight: '340px', background: '#07080c', borderRadius: '12px', position: 'relative', border: '1px solid rgba(255,255,255,0.03)' }}>
                <svg width="100%" height="100%" viewBox="0 0 500 340" style={{ display: 'block' }}>
                  {/* Draw connection lines */}
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
                        {/* Interactive flow animation */}
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

                  {/* Draw Nodes */}
                  {Object.entries(layout).map(([addr, details]) => {
                    // check if registered
                    const regState = data?.registry[addr.toLowerCase()]
                    const isRegistered = !!regState
                    const isActive = regState?.active

                    // check if dispute on any channel involving this node
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
                        {/* Glow effect backings */}
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
                        
                        {/* Solid base */}
                        <circle 
                          r="24" 
                          fill={isRegistered && isActive ? '#141621' : '#1e1f29'} 
                          stroke={nodeState === 'disputed' ? 'var(--accent-red)' : (nodeState === 'communicating' ? 'var(--accent-cyan)' : details.color)}
                          strokeWidth="2"
                        />

                        {/* Centered initial */}
                        <text 
                          textAnchor="middle" 
                          dy=".3em" 
                          fill="#fff" 
                          fontSize="13px" 
                          fontWeight="700"
                        >
                          {details.name.split(' ')[0]}
                        </text>

                        {/* Label */}
                        <text 
                          y="42" 
                          textAnchor="middle" 
                          fill="var(--text-primary)" 
                          fontSize="11px" 
                          fontWeight="600"
                        >
                          {details.name}
                        </text>

                        {/* Subtitle */}
                        <text 
                          y="56" 
                          textAnchor="middle" 
                          fill="var(--text-secondary)" 
                          fontSize="9.5px"
                        >
                          {details.role}
                        </text>
                      </g>
                    )
                  })}
                </svg>
              </div>
            </div>

            {/* State Channels / Gauges Monitor */}
            <div className="glass-card" style={{ padding: '24px', display: 'flex', flexDirection: 'column' }}>
              <h3 style={{ fontSize: '1.1rem', fontWeight: 600, color: '#fff', marginBottom: '16px' }}>State Channels Balance & Escrow</h3>
              
              <div style={{ flexGrow: 1, display: 'flex', flexDirection: 'column', gap: '20px' }}>
                {data && Object.keys(data.channels).length === 0 ? (
                  <div style={{ flexGrow: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', color: 'var(--text-dim)' }}>
                    <p style={{ fontSize: '0.9rem' }}>No open state channels found.</p>
                    <p style={{ fontSize: '0.8rem', marginTop: '4px' }}>Click 'Trigger Swarm' to fund payment tunnels.</p>
                  </div>
                ) : (
                  data && Object.entries(data.channels).map(([cid, chan]) => {
                    const senderName = layout[chan.sender.toLowerCase()]?.name.split(' ')[0] || chan.sender.slice(0, 6)
                    const recipientName = layout[chan.recipient.toLowerCase()]?.name.split(' ')[0] || chan.recipient.slice(0, 6)
                    
                    const isDisputed = chan.dispute && chan.dispute.active
                    const isChallenged = chan.challenged

                    const percent = 100 // Visual representation of funded channel

                    return (
                      <div key={cid} style={{ border: '1px solid rgba(255,255,255,0.03)', padding: '16px', borderRadius: '10px', background: 'rgba(0,0,0,0.15)' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '10px', fontSize: '0.85rem' }}>
                          <span style={{ fontWeight: 600 }}>{senderName} ➔ {recipientName}</span>
                          <span style={{ color: isDisputed ? 'var(--accent-red)' : (isChallenged ? 'var(--accent-orange)' : 'var(--accent-cyan)'), fontWeight: 600 }}>
                            {formatEth(chan.deposit)} ETH
                          </span>
                        </div>
                        
                        {/* Gauge bar */}
                        <div className="progress-meter" style={{ height: '8px', marginBottom: '8px' }}>
                          <div className="progress-meter-fill" style={{
                            width: `${percent}%`,
                            background: isDisputed ? 'var(--accent-red)' : (isChallenged ? 'var(--accent-orange)' : 'linear-gradient(90deg, var(--accent-cyan) 0%, var(--accent-purple) 100%)')
                          }}></div>
                        </div>

                        {/* Status Label */}
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: 'var(--text-dim)' }}>
                          <span>ID: {cid.slice(0, 10)}...</span>
                          {isDisputed ? (
                            <span style={{ color: 'var(--accent-red)', fontWeight: 600 }}>DISPUTED ACTIVE</span>
                          ) : isChallenged ? (
                            <span style={{ color: 'var(--accent-orange)', fontWeight: 600 }}>CHALLENGED (Expires in 1h)</span>
                          ) : (
                            <span style={{ color: 'var(--accent-green)' }}>SECURE State-Tunnel</span>
                          )}
                        </div>
                      </div>
                    )
                  })
                )}
              </div>
            </div>
          </div>

          {/* Logs Terminal & Controls */}
          <div style={{ display: 'grid', gridTemplateColumns: '7fr 5fr', gap: '32px' }}>
            
            {/* Logs Terminal */}
            <div className="glass-card" style={{ padding: '24px', display: 'flex', flexDirection: 'column' }}>
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

            {/* Swarm Controls */}
            <div className="glass-card" style={{ padding: '24px', display: 'flex', flexDirection: 'column' }}>
              <h3 style={{ fontSize: '1.1rem', fontWeight: 600, color: '#fff', marginBottom: '16px' }}>Economy Control Panel</h3>
              
              <div style={{ display: 'flex', flexDirection: 'column', gap: '20px', flexGrow: 1 }}>
                
                {/* Trigger Swarm Button */}
                <div>
                  <h4 style={{ fontSize: '0.9rem', color: 'var(--text-secondary)', marginBottom: '8px' }}>Swarm Automation</h4>
                  <button 
                    className="btn-cyber glow-purple-active" 
                    onClick={handleTriggerSwarm}
                    disabled={data?.is_running_swarm || isTriggering}
                    style={{ width: '100%', justifyContent: 'center', height: '48px' }}
                  >
                    {data?.is_running_swarm ? 'Swarm Running...' : 'Trigger Swarm Execution'}
                  </button>
                </div>

                {/* Time Travel Tool */}
                <div style={{ borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: '16px' }}>
                  <h4 style={{ fontSize: '0.9rem', color: 'var(--text-secondary)', marginBottom: '8px' }}>EVM Time Travel Simulation</h4>
                  <p style={{ fontSize: '0.8rem', color: 'var(--text-dim)', marginBottom: '12px', lineHeight: 1.4 }}>
                    Fast-forward block timestamp to force expire dispute challenge windows or standard challenge periods.
                  </p>
                  
                  <div style={{ display: 'flex', gap: '10px' }}>
                    <button 
                      onClick={() => handleTimeTravel(1)}
                      style={{
                        flex: 1,
                        background: '#1c1e2a',
                        border: '1px solid rgba(255,255,255,0.08)',
                        color: '#fff',
                        padding: '10px',
                        borderRadius: '6px',
                        cursor: 'pointer',
                        fontWeight: 600,
                        fontSize: '0.8rem'
                      }}
                    >
                      + 1 Hour
                    </button>
                    <button 
                      onClick={() => handleTimeTravel(24)}
                      style={{
                        flex: 1,
                        background: '#1c1e2a',
                        border: '1px solid rgba(255,255,255,0.08)',
                        color: '#fff',
                        padding: '10px',
                        borderRadius: '6px',
                        cursor: 'pointer',
                        fontWeight: 600,
                        fontSize: '0.8rem'
                      }}
                    >
                      + 24 Hours
                    </button>
                  </div>
                  
                  <div style={{ fontSize: '0.75rem', marginTop: '12px', color: 'var(--text-dim)', textAlign: 'center' }}>
                    Current Time Offset: {data ? `${(data.time_offset / 3600).toFixed(1)} hours` : '0h'}
                  </div>
                </div>

              </div>
            </div>

          </div>
        </>
      )}

    </div>
  )
}

export default App
