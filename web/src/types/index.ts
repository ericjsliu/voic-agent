export interface Message {
  id: string;
  type: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
  taskGraph?: any;
  l2Pending?: {
    taskId: string;
    stepId: string;
    branchId: string;
    action: string;
  };
}

export interface Writeback {
  task_id: string;
  step_id: string;
  branch_id: string;
  event: string;
  status: string;
  reason?: string;
  ts: string;
}

export interface VehicleTelemetry {
  gear: string;
  speed_kmh: number;
  latitude: number;
  longitude: number;
  windows_status: string;
  doors_locked: boolean;
  ac_on: boolean;
  ac_temp: number;
}

export interface ConnectionConfig {
  baseUrl: string;
  sessionId?: string;
  driverId: string;
  modelFilter?: string;
  versionFilter?: string;
}
