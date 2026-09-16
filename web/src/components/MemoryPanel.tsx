import { useState, useEffect } from 'react'
import './MemoryPanel.css'

interface Memory {
  memory_id: string;
  content: string;
  category: string;
  weight?: number;
  score?: number;
  created_at?: string;
  updated_at?: string;
}

interface MemoryPanelProps {
  baseUrl: string;
  userId: string; // account:driver format
  relevantMemories: Memory[]; // This-turn recall
}

function MemoryPanel({ baseUrl, userId, relevantMemories }: MemoryPanelProps) {
  const [allMemories, setAllMemories] = useState<Memory[]>([])
  const [loading, setLoading] = useState(false)
  const [selectedCategory, setSelectedCategory] = useState<string>('all')
  const [showRecall, setShowRecall] = useState(true)

  const categories = [
    { key: 'all', label: '全部' },
    { key: 'personal_basic', label: '个人基础' },
    { key: 'user_preference', label: '偏好' },
    { key: 'relationships', label: '人物关系' },
    { key: 'goals_plans', label: '目标计划' },
    { key: 'tasks_agreements', label: '任务约定' },
    { key: 'knowledge_experience', label: '知识经验' },
    { key: 'restrictions', label: '限制禁忌' },
    { key: 'health_habits', label: '健康习惯' },
    { key: 'items_devices', label: '物品设备' }
  ]

  useEffect(() => {
    if (userId) {
      fetchAllMemories()
    }
  }, [userId, selectedCategory])

  const fetchAllMemories = async () => {
    setLoading(true)
    try {
      const response = await fetch(`${baseUrl}/memory/list`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id: userId,
          category: selectedCategory === 'all' ? undefined : selectedCategory,
          limit: 100
        })
      })
      const data = await response.json()
      setAllMemories(data.memories || [])
    } catch (error) {
      console.error('Failed to fetch memories:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleDeleteMemory = async (memoryId: string) => {
    if (!confirm('确定要删除这条记忆吗？')) return

    try {
      const response = await fetch(`${baseUrl}/memory/delete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id: userId,
          memory_id: memoryId
        })
      })
      if (response.ok) {
        await fetchAllMemories()
      }
    } catch (error) {
      console.error('Failed to delete memory:', error)
    }
  }

  const handleClearAll = async () => {
    if (!confirm('确定要清空所有记忆吗？此操作不可恢复。')) return

    try {
      const response = await fetch(`${baseUrl}/memory/clear/${userId}`, {
        method: 'POST'
      })
      if (response.ok) {
        await fetchAllMemories()
      }
    } catch (error) {
      console.error('Failed to clear memories:', error)
    }
  }

  const formatCategory = (category: string) => {
    const cat = categories.find(c => c.key === category)
    return cat ? cat.label : category
  }

  return (
    <div className="memory-panel">
      <div className="memory-section">
        <div className="section-header">
          <h3>本轮召回 ({relevantMemories.length})</h3>
          <button 
            className="toggle-btn"
            onClick={() => setShowRecall(!showRecall)}
          >
            {showRecall ? '收起' : '展开'}
          </button>
        </div>
        
        {showRecall && (
          <div className="memory-list recall-list">
            {relevantMemories.length === 0 ? (
              <div className="empty-state">本轮未召回记忆</div>
            ) : (
              relevantMemories.map((mem, idx) => (
                <div key={idx} className="memory-item recall-item">
                  <div className="memory-category">{formatCategory(mem.category)}</div>
                  <div className="memory-content">{mem.content}</div>
                  {mem.score !== undefined && (
                    <div className="memory-meta">
                      <span className="memory-score">相关度: {(mem.score * 100).toFixed(0)}%</span>
                      {mem.weight && <span className="memory-weight">权重: {mem.weight.toFixed(1)}</span>}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        )}
      </div>

      <div className="memory-section">
        <div className="section-header">
          <h3>全部记忆 ({allMemories.length})</h3>
          <button 
            className="danger-btn"
            onClick={handleClearAll}
            disabled={allMemories.length === 0}
          >
            清空全部
          </button>
        </div>

        <div className="category-filter">
          {categories.map(cat => (
            <button
              key={cat.key}
              className={`filter-btn ${selectedCategory === cat.key ? 'active' : ''}`}
              onClick={() => setSelectedCategory(cat.key)}
            >
              {cat.label}
            </button>
          ))}
        </div>

        <div className="memory-list all-list">
          {loading ? (
            <div className="loading-state">加载中...</div>
          ) : allMemories.length === 0 ? (
            <div className="empty-state">暂无记忆</div>
          ) : (
            allMemories.map(mem => (
              <div key={mem.memory_id} className="memory-item">
                <div className="memory-header">
                  <span className="memory-category">{formatCategory(mem.category)}</span>
                  <button
                    className="delete-btn"
                    onClick={() => handleDeleteMemory(mem.memory_id)}
                  >
                    ✕
                  </button>
                </div>
                <div className="memory-content">{mem.content}</div>
                {mem.updated_at && (
                  <div className="memory-meta">
                    <span className="memory-date">
                      {new Date(mem.updated_at).toLocaleString('zh-CN')}
                    </span>
                    {mem.weight && <span className="memory-weight">权重: {mem.weight.toFixed(1)}</span>}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}

export default MemoryPanel
