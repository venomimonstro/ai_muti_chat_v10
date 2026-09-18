"use client";

import type {Conversation} from "../../lib/types";
import {MessageCard} from "./MessageCard";

export function ChatThread({conversation,onConversation}:{conversation:Conversation|null;onConversation:(value:Conversation)=>void}){
  if(!conversation||conversation.messages.length===0)return <section className="proEmpty"><span>✦</span><h1>Чем займёмся?</h1><p>AUTO может подобрать модель автоматически. Или выберите конкретную модель вручную.</p><div><button data-starter="Составь маркетинговую стратегию">Маркетинговая стратегия</button><button data-starter="Проанализируй документ и выдели главное">Анализ документа</button><button data-starter="Помоги написать и проверить код">Код</button><button data-starter="Сравни варианты решения и риски">Сравнение решений</button></div></section>;
  return <section className="proThread">{conversation.messages.map(message=><MessageCard key={message.id} conversationId={conversation.id} message={message} onConversation={onConversation}/>)}</section>;
}
