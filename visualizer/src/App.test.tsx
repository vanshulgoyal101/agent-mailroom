import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import App from './App'

// Mock state payload representing active agents and state channel disputes
const mockStatePayload = {
  registry: {
    "0x35da118ee4b6a8301881b4c8b7761025107c64c4": {
      owner: "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
      endpoint: "http://127.0.0.1:8001",
      modelCapabilities: "buyer",
      ratePerTaskWei: 0,
      active: true,
      stake: 100000000000000000
    },
    "0xd79396dc9b3b10cadfaa601485afd5cd4887c4d1": {
      owner: "0xF9e6FAC08F69c01B217aC4D7E4d1c4e4836f7e19",
      endpoint: "http://127.0.0.1:8003",
      modelCapabilities: "refactor",
      ratePerTaskWei: 10000000000000000,
      active: true,
      stake: 100000000000000000
    }
  },
  channels: {
    "0x673bc78db7f61b3d5d8fe3bdbf1825343413fc99396806f995e0942834c1cf9c": {
      sender: "0x1752fF3BDd7E3bb40aADa7aAD612Db4B081f83F0",
      recipient: "0xd79396Dc9B3B10caDfAA601485AFD5CD4887C4d1",
      deposit: 50000000000000000,
      challengeExpiry: 0,
      challenged: false,
      dispute: {
        taskHash: "0x",
        expiry: 0,
        active: false
      }
    }
  },
  stakes: {
    "0x35da118ee4b6a8301881b4c8b7761025107c64c4": 100000000000000000,
    "0xd79396dc9b3b10cadfaa601485afd5cd4887c4d1": 100000000000000000
  },
  time_offset: 3600,
  logs: [
    "[12:00:00] Initializing Agent Mailrooms...",
    "[12:00:01] Swarm economy coordination settled successfully."
  ],
  is_running_swarm: false,
  simulated_vouchers: {
    "0x673bc78db7f61b3d5d8fe3bdbf1825343413fc99396806f995e0942834c1cf9c": 10000000000000000
  }
}

describe('AgentMailroom Visualizer UI Tests', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((url) => {
      if (url.includes('/api/state')) {
        return Promise.resolve({
          json: () => Promise.resolve(mockStatePayload)
        })
      }
      return Promise.resolve({
        json: () => Promise.resolve({ status: 'success' })
      })
    }))
  })

  it('renders metrics and topology title successfully', async () => {
    render(<App />)
    
    // Verify connection status
    await waitFor(() => {
      expect(screen.getByText('EVM MOCK NODE ONLINE')).toBeTruthy()
    })

    // Verify metrics numbers
    expect(screen.getByText('2 AGENTS')).toBeTruthy()
    expect(screen.getByText('0.2000 ETH')).toBeTruthy() // Total collateral staked
    expect(screen.getByText('1 OPEN')).toBeTruthy() // Active channels count
    expect(screen.getByText('+ 1.0 hrs')).toBeTruthy() // Time offset
  })

  it('switches between tabs successfully', async () => {
    render(<App />)
    
    // Default tab is Swarm Automation
    await waitFor(() => {
      expect(screen.getByText('Network Node Topology Map')).toBeTruthy()
    })

    // Click Agent Registry tab
    const registryTabButton = screen.getByText('Agent Registry Manager')
    fireEvent.click(registryTabButton)

    await waitFor(() => {
      expect(screen.getByText('On-Chain DID Registrations')).toBeTruthy()
    })

    // Click Manual Tunnel Playground tab
    const playgroundTabButton = screen.getByText('Manual Tunnel Playground')
    fireEvent.click(playgroundTabButton)

    await waitFor(() => {
      expect(screen.getByText('State-Tunnel Playground')).toBeTruthy()
    })
  })

  it('triggers swarm automation click', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (url.includes('/api/state')) {
        return Promise.resolve({
          json: () => Promise.resolve(mockStatePayload)
        })
      }
      return Promise.resolve({
        json: () => Promise.resolve({ status: 'started' })
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)

    const triggerButton = await screen.findByText('Trigger Swarm Execution')
    fireEvent.click(triggerButton)

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:8545/api/run-swarm', { method: 'POST' })
    })
  })
})
