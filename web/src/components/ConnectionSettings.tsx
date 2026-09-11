import { ConnectionConfig } from '../types'
import './ConnectionSettings.css'

interface Props {
  config: ConnectionConfig;
  onChange: (config: ConnectionConfig) => void;
  onCreateSession: () => void;
}

export default function ConnectionSettings({ config, onChange, onCreateSession }: Props) {
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
      
      <div className="settings-row-inline">
        <div className="settings-col">
          <label>Model</label>
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
          <label>Version</label>
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
