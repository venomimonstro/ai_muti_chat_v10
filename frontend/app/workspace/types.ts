export type ConversationSummary={id:string;title:string;routing_mode:"manual"|"economy"|"balanced"|"maximum";selected_model:string;project:string|null;folder:string|null;is_pinned:boolean;created_at:string;updated_at:string};
export type ConversationFolder={id:string;name:string;is_pinned:boolean;position:number;conversation_count:number;created_at:string;updated_at:string};
export type DraftState={content:string;updatedAt:number;serverVersion:number};
