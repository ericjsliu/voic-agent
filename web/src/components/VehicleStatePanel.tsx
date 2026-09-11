import { useState, useEffect } from 'react'
import { VehicleTelemetry } from '../types'
import './VehicleStatePanel.css'

interface Props {
  telemetry: VehicleTelemetry | null;
  onTelemetryChange: (telemetry: VehicleTelemetry) => void;
}

export default function VehicleStatePanel({ telemetry, onTelemetryChange }: Props) {
  const [localState, setLocalState] = useState<VehicleTelemetry>({
    gear: 'P',
    speed_kmh: 0,
    latitude: 39.9042,
    longitude: 116.4074,
    windows_status: 'closed',
    doors_locked: true,
    ac_on: false,
    ac_temp: 24
  })

  useEffect(() => {
    if (telemetry) {
      setLocalState(telemetry)
    }
  }, [telemetry])

  const handleGearChange = (gear: string) => {
    const newState = { ...localState, gear }
    setLocalState(newState)
    onTelemetryChange(newState)
  }

  const handleSpeedChange = (speed: number) => {
    const newState = { ...localState, speed_kmh: Math.max(0, speed) }
    setLocalState(newState)
    onTelemetryChange(newState)
  }

  const toggleWindows = () => {
    const newState = { 
      ...localState, 
      windows_status: localState.windows_status === 'open' ? 'closed' : 'open' 
    }
    setLocalState(newState)
    onTelemetryChange(newState)
  }

  const toggleDoors = () => {
    const newState = { ...localState, doors_locked: !localState.doors_locked }
    setLocalState(newState)
    onTelemetryChange(newState)
  }

  const toggleAC = () => {
    const newState = { ...localState, ac_on: !localState.ac_on }
    setLocalState(newState)
    onTelemetryChange(newState)
  }

  return (
    <div className="vehicle-state-panel">
      <div className="panel-header">
        <h3>🚗 Vehicle State (Mock)</h3>
        <span className="hint">Edit to test L1 rejection</span>
      </div>

      <div className="state-grid">
        <div className="state-item">
          <label>Gear</label>
          <div className="gear-selector">
            {['P', 'R', 'N', 'D'].map(g => (
              <button
                key={g}
                className={`gear-btn ${localState.gear === g ? 'active' : ''}`}
                onClick={() => handleGearChange(g)}
              >
                {g}
              </button>
            ))}
          </div>
        </div>

        <div className="state-item">
          <label>Speed (km/h)</label>
          <div className="speed-control">
            <button onClick={() => handleSpeedChange(localState.speed_kmh - 10)}>-</button>
            <input
              type="number"
              value={localState.speed_kmh}
              onChange={(e) => handleSpeedChange(parseInt(e.target.value) || 0)}
              min="0"
              max="200"
            />
            <button onClick={() => handleSpeedChange(localState.speed_kmh + 10)}>+</button>
          </div>
        </div>

        <div className="state-item">
          <label>Windows</label>
          <button 
            className={`toggle-btn ${localState.windows_status === 'open' ? 'active' : ''}`}
            onClick={toggleWindows}
          >
            {localState.windows_status === 'open' ? '🔓 Open' : '🔒 Closed'}
          </button>
        </div>

        <div className="state-item">
          <label>Doors</label>
          <button 
            className={`toggle-btn ${!localState.doors_locked ? 'active' : ''}`}
            onClick={toggleDoors}
          >
            {localState.doors_locked ? '🔒 Locked' : '🔓 Unlocked'}
          </button>
        </div>

        <div className="state-item">
          <label>AC</label>
          <button 
            className={`toggle-btn ${localState.ac_on ? 'active' : ''}`}
            onClick={toggleAC}
          >
            {localState.ac_on ? `✓ ON (${localState.ac_temp}°C)` : '✕ OFF'}
          </button>
        </div>

        <div className="state-item">
          <label>Location</label>
          <div className="location-display">
            {localState.latitude.toFixed(4)}, {localState.longitude.toFixed(4)}
          </div>
        </div>
      </div>

      <div className="validation-hint">
        <strong>L1 Rejection Test:</strong>
        <ul>
          <li>Gear ≠ P: door_lock/trunk_open → <span className="rejected">rejected</span></li>
          <li>Speed &gt; 0: sunroof_open → <span className="rejected">rejected</span></li>
        </ul>
      </div>
    </div>
  )
}
