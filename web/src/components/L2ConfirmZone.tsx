import { useState, useEffect } from 'react'
import './L2ConfirmZone.css'

interface Props {
  pendingConfirm?: {
    taskId: string;
    stepId: string;
    branchId: string;
    action: string;
    description?: string;
  };
  onConfirm: (taskId: string, stepId: string, branchId: string, accepted: boolean) => void;
}

export default function L2ConfirmZone({ pendingConfirm, onConfirm }: Props) {
  const [countdown, setCountdown] = useState(15)
  const [startTime, setStartTime] = useState<number | null>(null)

  useEffect(() => {
    if (pendingConfirm) {
      setCountdown(15)
      setStartTime(Date.now())
    } else {
      setStartTime(null)
    }
  }, [pendingConfirm])

  useEffect(() => {
    if (!pendingConfirm || !startTime) return

    const timer = setInterval(() => {
      const elapsed = Math.floor((Date.now() - startTime) / 1000)
      const remaining = Math.max(0, 15 - elapsed)
      setCountdown(remaining)

      if (remaining === 0) {
        // Timeout handled by backend, just clear UI
        clearInterval(timer)
      }
    }, 100)

    return () => clearInterval(timer)
  }, [pendingConfirm, startTime])

  if (!pendingConfirm) {
    return (
      <div className="l2-confirm-zone empty">
        <div className="empty-state">
          <span className="icon">🔒</span>
          <span>No pending L2 confirmation</span>
        </div>
      </div>
    )
  }

  const handleConfirm = (accepted: boolean) => {
    onConfirm(
      pendingConfirm.taskId,
      pendingConfirm.stepId,
      pendingConfirm.branchId,
      accepted
    )
  }

  const progress = (countdown / 15) * 100

  return (
    <div className="l2-confirm-zone active">
      <div className="confirm-header">
        <span className="badge-l2">L2 High-Risk Action</span>
        <span className={`countdown ${countdown <= 5 ? 'urgent' : ''}`}>
          {countdown}s
        </span>
      </div>

      <div className="confirm-content">
        <h4>{pendingConfirm.description || pendingConfirm.action}</h4>
        <p>This action requires your confirmation to proceed.</p>
      </div>

      <div className="progress-bar">
        <div 
          className="progress-fill" 
          style={{ width: `${progress}%` }}
        />
      </div>

      <div className="confirm-actions">
        <button 
          className="btn-confirm-large"
          onClick={() => handleConfirm(true)}
          disabled={countdown === 0}
        >
          <span className="icon">✓</span>
          <span>确认执行</span>
        </button>
        <button 
          className="btn-cancel-large"
          onClick={() => handleConfirm(false)}
          disabled={countdown === 0}
        >
          <span className="icon">✕</span>
          <span>取消操作</span>
        </button>
      </div>

      {countdown === 0 && (
        <div className="timeout-notice">
          ⏱️ Confirmation timeout - operation cancelled
        </div>
      )}
    </div>
  )
}
