import { ConnectionConfig } from '../types'
import './ConnectionSettings.css'

interface Props {
  config: ConnectionConfig;
  onChange: (config: ConnectionConfig) => void;
  onCreateSession: () => void;
  profileState?: 'ready' | 'switching' | 'failed';
  onSwitchModel?: (modelId: string) => void;
}

export default function ConnectionSettings({ config, onChange, onCreateSession, profileState = 'ready', onSwitchModel }: Props) {
  return (
    <div className="connection-settings">
      <div className="settings-row">
        <label>Base URL</label>
        <input
          type="text"
          value={config.baseUrl}
          onChange={(e) => onChange({ ...config, baseUrl: e.target.value })}
          placeholder="http://localhost:8000"
        />
      </div>
      
      <div className="settings-row">
        <label>Driver ID</label>
        <input
          type="text"
          value={config.driverId}
          onChange={(e) => onChange({ ...config, driverId: e.target.value })}
          placeholder="driver_001"
        />
      </div>
      
      <div className="settings-row">
        <label>Vehicle Model (Capability Profile)</label>
        <div className="model-select-container">
          <select
            value={config.vehicleModel}
            onChange={(e) => {
              const newModel = e.target.value
              onChange({ ...config, vehicleModel: newModel })
              // 如果session已存在，调用切换API
              if (config.sessionId && onSwitchModel) {
                onSwitchModel(newModel)
              }
            }}
            disabled={profileState === 'switching'}
          >
            <option value="model_a">Model A (Standard) - Basic Features</option>
            <option value="model_b">Model B (Premium) - Full Features</option>
          </select>
          
          {profileState && (
            <div className={`profile-state profile-state-${profileState}`}>
              {profileState === 'ready' && '✅ Ready'}
              {profileState === 'switching' && '⏳ Switching...'}
              {profileState === 'failed' && '❌ Failed'}
            </div>
          )}
        </div>
        
        <div className="model-hint">
          {config.vehicleModel === 'model_a' && '🚗 Supports: window, door, AC control'}
          {config.vehicleModel === 'model_b' && '🚙 Supports: + sunroof, seat heating, power trunk'}
        </div>
      </div>
      
      <div className="settings-row-inline">
        <div className="settings-col">
          <label>RAG Model Filter</label>
          <select
            value={config.modelFilter || ''}
            onChange={(e) => onChange({ ...config, modelFilter: e.target.value || undefined })}
          >
            <option value="">All</option>
            <option value="ModelA">ModelA</option>
            <option value="ModelB">ModelB</option>
          </select>
        </div>
        
        <div className="settings-col">
          <label>RAG Version</label>
          <input
            type="text"
            value={config.versionFilter || ''}
            onChange={(e) => onChange({ ...config, versionFilter: e.target.value || undefined })}
            placeholder="2024"
          />
        </div>
      </div>
      
      {config.sessionId && (
        <div className="session-info">
          Session: <code>{config.sessionId}</code>
        </div>
      )}
      
      {!config.sessionId && (
        <button className="create-session-btn" onClick={onCreateSession}>
          Create Session
        </button>
      )}
    </div>
  )
}
