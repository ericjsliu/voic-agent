import { useState } from 'react'
import { Writeback, VehicleTelemetry } from '../types'
import './InfoPanel.css'

interface Props {
  taskGraph: any;
  writebacks: Writeback[];
  telemetry: VehicleTelemetry | null;
}

export default function InfoPanel({ taskGraph, writebacks, telemetry }: Props) {
  const [activeTab, setActiveTab] = useState<'taskgraph' | 'writebacks' | 'telemetry'>('taskgraph')

  return (
    <div className="info-panel">
      <div className="tabs">
        <button
          className={`tab ${activeTab === 'taskgraph' ? 'active' : ''}`}
          onClick={() => setActiveTab('taskgraph')}
        >
          TaskGraph
        </button>
        <button
          className={`tab ${activeTab === 'writebacks' ? 'active' : ''}`}
          onClick={() => setActiveTab('writebacks')}
        >
          Writebacks
          {writebacks.length > 0 && <span className="badge">{writebacks.length}</span>}
        </button>
        <button
          className={`tab ${activeTab === 'telemetry' ? 'active' : ''}`}
          onClick={() => setActiveTab('telemetry')}
        >
          Telemetry
        </button>
      </div>

      <div className="panel-content">
        {activeTab === 'taskgraph' && (
          <div className="tab-content">
            {taskGraph ? (
              <pre className="json-view">{JSON.stringify(taskGraph, null, 2)}</pre>
            ) : (
              <div className="empty-state">暂无 TaskGraph</div>
            )}
          </div>
        )}

        {activeTab === 'writebacks' && (
          <div className="tab-content">
            {writebacks.length > 0 ? (
              <div className="writebacks-list">
                {writebacks.map((wb, idx) => (
                  <div 
                    key={idx} 
                    className={`writeback-item ${
                      wb.status === 'rejected' || wb.status === 'timeout' ? 'highlighted' : ''
                    }`}
                  >
                    <div className="writeback-header">
                      <span className={`event-badge ${wb.event}`}>{wb.event}</span>
                      <span className={`status-badge ${wb.status}`}>{wb.status}</span>
                    </div>
                    <div className="writeback-details">
                      <div><strong>Task:</strong> {wb.task_id.substring(0, 8)}...</div>
                      <div><strong>Step:</strong> {wb.step_id}</div>
                      {wb.reason && (
                        <div className={`reason ${wb.status === 'rejected' || wb.status === 'timeout' ? 'error' : ''}`}>
                          {wb.status === 'rejected' && '🚫 '}
                          {wb.status === 'timeout' && '⏱️ '}
                          {wb.reason}
                        </div>
                      )}
                    </div>
                    <div className="writeback-time">
                      {new Date(wb.ts).toLocaleString()}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="empty-state">暂无 Writeback</div>
            )}
          </div>
        )}

        {activeTab === 'telemetry' && (
          <div className="tab-content">
            {telemetry ? (
              <div className="telemetry-grid">
                <div className="telemetry-item">
                  <div className="label">档位</div>
                  <div className="value">{telemetry.gear}</div>
                </div>
                <div className="telemetry-item">
                  <div className="label">车速</div>
                  <div className="value">{telemetry.speed_kmh} km/h</div>
                </div>
                <div className="telemetry-item">
                  <div className="label">位置</div>
                  <div className="value small">
                    {telemetry.latitude.toFixed(4)}, {telemetry.longitude.toFixed(4)}
                  </div>
                </div>
                <div className="telemetry-item">
                  <div className="label">车窗</div>
                  <div className="value">{telemetry.windows_status}</div>
                </div>
                <div className="telemetry-item">
                  <div className="label">门锁</div>
                  <div className={`value ${telemetry.doors_locked ? 'locked' : 'unlocked'}`}>
                    {telemetry.doors_locked ? '🔒 已锁' : '🔓 未锁'}
                  </div>
                </div>
                <div className="telemetry-item">
                  <div className="label">空调</div>
                  <div className="value">
                    {telemetry.ac_on ? `开 (${telemetry.ac_temp}°C)` : '关'}
                  </div>
                </div>
              </div>
            ) : (
              <div className="empty-state">暂无遥测数据</div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
