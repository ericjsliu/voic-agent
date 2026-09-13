import { useState, useEffect, useRef } from 'react'
import ChatPanel from './components/ChatPanel'
import InfoPanel from './components/InfoPanel'
import ConnectionSettings from './components/ConnectionSettings'
import VehicleStatePanel from './components/VehicleStatePanel'
import L2ConfirmZone from './components/L2ConfirmZone'
import { Message, Writeback, VehicleTelemetry, ConnectionConfig } from './types'
import './App.css'

function App() {
  const [messages, setMessages] = useState<Message[]>([])
  const [writebacks, setWritebacks] = useState<Writeback[]>([])
  const [telemetry, setTelemetry] = useState<VehicleTelemetry>({
    gear: 'P',
    speed_kmh: 0,
    latitude: 39.9042,
    longitude: 116.4074,
    windows_status: 'closed',
    doors_locked: true,
    ac_on: false,
    ac_temp: 24
  })
  const [currentTaskGraph, setCurrentTaskGraph] = useState<any>(null)
  const [pendingL2, setPendingL2] = useState<any>(null)
  const [connected, setConnected] = useState(false)
  const [profileState, setProfileState] = useState<'ready' | 'switching' | 'failed'>('ready')
  const [config, setConfig] = useState<ConnectionConfig>({
    baseUrl: 'http://localhost:8000',
    driverId: 'driver_001',
    vehicleModel: 'model_a',
    modelFilter: 'ModelA',
    versionFilter: '2024'
  })
  const [capabilityProfile, setCapabilityProfile] = useState<{
    model_id: string;
    model_name: string;
    supported_actions: string[];
  } | null>(null)
  
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    if (config.sessionId) {
      connectWebSocket(config.sessionId)
      fetchCapabilityProfile(config.sessionId)
    }
    return () => {
      wsRef.current?.close()
    }
  }, [config.sessionId])

  // 获取当前会话的Capability Profile
  const fetchCapabilityProfile = async (sessionId: string) => {
    try {
      const response = await fetch(`${config.baseUrl}/session/${sessionId}/capability_profile`)
      if (response.ok) {
        const profile = await response.json()
        setCapabilityProfile(profile)
        console.log('[UI] Loaded capability profile:', profile.model_id)
      }
    } catch (error) {
      console.error('[UI] Failed to fetch capability profile:', error)
    }
  }

  const connectWebSocket = (sessionId: string) => {
    const wsUrl = config.baseUrl.replace('http', 'ws') + `/ws/${sessionId}`
    const ws = new WebSocket(wsUrl)
    
    ws.onopen = () => {
      setConnected(true)
      console.log('WebSocket connected')
    }
    
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)
      
      if (data.type === 'taskgraph') {
        setCurrentTaskGraph(data.data)
        
        // 检查是否有L2待确认
        const l2Step = data.data.tasks?.[0]?.steps?.find((s: any) => 
          s.action?.level === 'L2'
        )
        
        if (l2Step) {
          const l2Info = {
            taskId: data.data.tasks[0].task_id,
            stepId: l2Step.step_id,
            branchId: data.data.tasks[0].branch_id,
            traceId: data.trace_id || data.data.trace_id,  // P0 fix #2: capture trace_id
            action: l2Step.action?.action,
            description: l2Step.description || l2Step.action?.action
          }
          setPendingL2(l2Info)
          
          setMessages(prev => [...prev, {
            id: Date.now().toString(),
            type: 'system',
            content: `⚠️ 需要确认：${l2Info.description}`,
            timestamp: new Date().toISOString()
          }])
        }
      } else if (data.type === 'writeback') {
        const wb = data.data
        setWritebacks(prev => [wb, ...prev].slice(0, 30))
        
        // 处理L2确认结果
        if (wb.event === 'confirm_result') {
          if (wb.status === 'accepted' || wb.status === 'declined' || wb.status === 'timeout') {
            setPendingL2(null)
            
            const statusText = {
              'accepted': '✓ 已确认执行',
              'declined': '✕ 已取消操作',
              'timeout': '⏱️ 确认超时 - 已取消'
            }[wb.status] || '处理完成'
            
            setMessages(prev => [...prev, {
              id: Date.now().toString(),
              type: 'system',
              content: statusText,
              timestamp: new Date().toISOString()
            }])
          }
        }
      } else if (data.type === 'profile_state') {
        // 处理Profile状态变化（detailed-v1.5）
        const { state, message } = data.data
        setProfileState(state)
        
        // Profile ready后重新加载capability profile（UI刷新chips）
        if (state === 'ready' && config.sessionId) {
          fetchCapabilityProfile(config.sessionId)
        }
        
        const stateIcons = {
          'switching': '⏳',
          'ready': '✅',
          'failed': '❌'
        }
        
        setMessages(prev => [...prev, {
          id: Date.now().toString(),
          type: 'system',
          content: `${stateIcons[state as keyof typeof stateIcons] || '🔄'} ${message}`,
          timestamp: new Date().toISOString()
        }])
      }
    }
    
    ws.onclose = () => {
      setConnected(false)
      console.log('WebSocket disconnected')
    }
    
    wsRef.current = ws
  }

  const handleSendMessage = async (text: string) => {
    // 添加用户消息
    const userMsg: Message = {
      id: Date.now().toString(),
      type: 'user',
      content: text,
      timestamp: new Date().toISOString()
    }
    setMessages(prev => [...prev, userMsg])

    try {
      const response = await fetch(`${config.baseUrl}/dialogue`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: config.sessionId,
          driver_id: config.driverId,
          utterance: text,
          telemetry: telemetry
        })
      })

      const data = await response.json()
      
      if (!config.sessionId) {
        setConfig(prev => ({ ...prev, sessionId: data.session_id }))
      }

      // 提取知识查询的citations
      const knowledgeStep = data.taskgraph.tasks?.[0]?.steps?.find((s: any) => s.domain === 'knowledge')
      const citations = knowledgeStep?.action?.citations

      // 添加助手回复
      const assistantMsg: Message = {
        id: (Date.now() + 1).toString(),
        type: 'assistant',
        content: generateResponseText(data.taskgraph),
        timestamp: new Date().toISOString(),
        taskGraph: data.taskgraph,
        citations: citations
      }
      setMessages(prev => [...prev, assistantMsg])
      setCurrentTaskGraph(data.taskgraph)

    } catch (error) {
      console.error('Failed to send message:', error)
      setMessages(prev => [...prev, {
        id: (Date.now() + 1).toString(),
        type: 'system',
        content: '发送失败，请检查连接',
        timestamp: new Date().toISOString()
      }])
    }
  }

  const generateResponseText = (taskgraph: any): string => {
    const task = taskgraph.tasks?.[0]
    if (!task) return '好的'
    
    const steps = task.steps || []
    if (steps.length === 0) return '好的'
    
    const descriptions = steps.map((s: any) => 
      s.description || s.action?.action || '执行任务'
    ).join('，')
    
    return `好的，正在${descriptions}`
  }

  const handleL2Confirm = (taskId: string, stepId: string, branchId: string, traceId: string | undefined, accepted: boolean) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: 'l2_confirm',
        task_id: taskId,
        step_id: stepId,
        branch_id: branchId,
        trace_id: traceId,  // P0 fix #2: include trace_id
        accepted
      }))
      
      setMessages(prev => [...prev, {
        id: Date.now().toString(),
        type: 'system',
        content: accepted ? '已确认执行' : '已取消操作',
        timestamp: new Date().toISOString()
      }])
    }
  }

  const handleCreateSession = async () => {
    try {
      const response = await fetch(`${config.baseUrl}/session/create`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          driver_id: config.driverId,
          vehicle_id: 'vehicle_001',
          vehicle_model: config.vehicleModel
        })
      })
      const data = await response.json()
      setConfig(prev => ({ ...prev, sessionId: data.session_id }))
      setMessages([{
        id: '0',
        type: 'system',
        content: `会话已创建: ${data.session_id}`,
        timestamp: new Date().toISOString()
      }])
    } catch (error) {
      console.error('Failed to create session:', error)
    }
  }

  const handleSwitchVehicleModel = async (newModelId: string) => {
    if (!config.sessionId) {
      console.error('No active session')
      return
    }

    try {
      setProfileState('switching')
      
      const response = await fetch(`${config.baseUrl}/session/${config.sessionId}/switch_vehicle_model`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model_id: newModelId
        })
      })
      
      if (!response.ok) {
        const error = await response.json()
        throw new Error(error.detail || '切换失败')
      }
      
      const data = await response.json()
      setConfig(prev => ({ ...prev, vehicleModel: newModelId }))
      
      // WebSocket会收到profile_state消息自动更新状态
      
    } catch (error) {
      console.error('Failed to switch vehicle model:', error)
      setProfileState('failed')
      setMessages(prev => [...prev, {
        id: Date.now().toString(),
        type: 'system',
        content: `❌ 切换车型失败: ${error}`,
        timestamp: new Date().toISOString()
      }])
    }
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>🚗 Smart Cockpit Voice Agent</h1>
        <div className="connection-status">
          <span className={`status-dot ${connected ? 'connected' : 'disconnected'}`} />
          {connected ? 'Connected' : 'Disconnected'}
        </div>
      </header>
      
      <div className="app-body">
        <div className="left-panel">
          <ConnectionSettings 
            config={config}
            onChange={setConfig}
            onCreateSession={handleCreateSession}
            profileState={profileState}
            onSwitchModel={handleSwitchVehicleModel}
          />
          <VehicleStatePanel
            telemetry={telemetry}
            onTelemetryChange={setTelemetry}
          />
          <L2ConfirmZone
            pendingConfirm={pendingL2}
            onConfirm={handleL2Confirm}
          />
          <ChatPanel 
            messages={messages}
            onSend={handleSendMessage}
            onL2Confirm={handleL2Confirm}
            capabilityProfile={capabilityProfile || undefined}
          />
        </div>
        
        <div className="right-panel">
          <InfoPanel 
            taskGraph={currentTaskGraph}
            writebacks={writebacks}
            telemetry={telemetry}
          />
        </div>
      </div>
    </div>
  )
}

export default App
