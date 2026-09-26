"use client";

import {useState} from "react";

type Node={id:string;title:string;type:string;prompt?:string;status?:"draft"|"publish";notification_title?:string;message?:string;condition_source?:"previous_text"|"objective";operator?:"contains"|"not_contains"|"is_empty"|"not_empty";value?:string;on_true?:string;on_false?:string;wait_minutes?:number};
type Graph={version?:number;nodes:Node[];edges:Array<{from:string;to:string}>};
type Props={disabled?:boolean;currentNodeCount:number;onApply:(graph:Graph)=>Promise<void>};

type Recipe={slug:string;name:string;description:string;nodes:Node[]};

const recipes:Recipe[]=[
 {slug:"content-approval",name:"Контент с согласованием",description:"Найти актуальную тему, написать материал, проверить качество и показать вам перед дальнейшими действиями.",nodes:[
  {id:"research",title:"Изучить тему",type:"web",prompt:"Найди актуальные факты, новости, вопросы аудитории и сильные темы по текущей задаче. Используй только подтверждённые источники."},
  {id:"draft",title:"Подготовить материал",type:"llm",prompt:"На основе исследования подготовь готовый материал под задачу пользователя. Пиши понятно, конкретно и без выдуманных фактов."},
  {id:"review",title:"Проверить качество",type:"review",prompt:"Проверь материал: факты, логика, повторы, соответствие задаче и готовность для реального пользователя. Исправь найденные слабые места."},
  {id:"approval",title:"Показать пользователю",type:"approval"},
  {id:"notify",title:"Сообщить о готовности",type:"notify",notification_title:"Материал готов к проверке",message:"Материал подготовлен и прошёл внутреннюю проверку."},
  {id:"finish",title:"Завершить",type:"finish"},
 ]},
 {slug:"seo-wordpress",name:"SEO-статья → WordPress",description:"Исследовать тему, написать и проверить статью, запросить подтверждение и сохранить её в WordPress как черновик.",nodes:[
  {id:"research",title:"Собрать факты и интент",type:"research",prompt:"Исследуй тему, реальные вопросы аудитории, поисковый интент и факты. Не выдумывай частотность или данные, которых нет в источниках."},
  {id:"web",title:"Проверить актуальность",type:"web",prompt:"Найди свежие надёжные источники и уточни актуальные данные, которые нужны для статьи."},
  {id:"files",title:"Изучить материалы проекта",type:"files",prompt:"Используй материалы проекта, чтобы статья соответствовала продукту, компании и уже существующему контенту."},
  {id:"article",title:"Написать SEO-статью",type:"llm",prompt:"Напиши полезную SEO-статью для человека: один H1, логичная структура, конкретные ответы, без переспама и выдуманных обещаний."},
  {id:"review",title:"Редакторская проверка",type:"review",prompt:"Проверь факты, структуру, дубли, рекламные обещания и естественность текста. Верни финальную версию статьи."},
  {id:"approval",title:"Подтвердить публикацию",type:"approval"},
  {id:"publish",title:"Сохранить в WordPress",type:"publish",status:"draft"},
  {id:"finish",title:"Завершить",type:"finish"},
 ]},
 {slug:"monitor-alert",name:"Мониторинг и уведомление",description:"Проверить свежие данные и сообщить вам результат. Подходит для конкурентов, цен, новостей и изменений.",nodes:[
  {id:"web",title:"Проверить актуальные данные",type:"web",prompt:"Проверь актуальные данные по задаче пользователя. Зафиксируй, что изменилось и какие источники это подтверждают."},
  {id:"analysis",title:"Оценить изменения",type:"analytics",prompt:"Сравни найденные данные с контекстом задачи. Выдели только существенные изменения, риски и возможности."},
  {id:"notify",title:"Отправить результат",type:"notify",notification_title:"Мониторинг завершён",message:"Проверка завершена. Откройте запуск, чтобы посмотреть найденные изменения."},
  {id:"finish",title:"Завершить",type:"finish"},
 ]},
 {slug:"research-report",name:"Исследование → отчёт",description:"Собрать web-данные и материалы проекта, провести анализ и подготовить проверенный отчёт.",nodes:[
  {id:"web",title:"Найти свежие источники",type:"web",prompt:"Собери свежие и релевантные источники по задаче. Отделяй факты от мнений."},
  {id:"files",title:"Изучить внутренние материалы",type:"files",prompt:"Изучи связанные материалы проекта и найди данные, которые нужно сопоставить с внешними источниками."},
  {id:"analysis",title:"Проанализировать",type:"analytics",prompt:"Сопоставь внешние и внутренние данные, найди закономерности, противоречия, риски и практические выводы."},
  {id:"review",title:"Проверить выводы",type:"review",prompt:"Проверь, что выводы действительно следуют из данных, а неопределённость и ограничения явно обозначены."},
  {id:"report",title:"Подготовить итог",type:"llm",prompt:"Собери финальный структурированный отчёт: выводы, доказательства, риски и конкретные следующие действия."},
  {id:"finish",title:"Завершить",type:"finish"},
 ]},
];

function graphFor(recipe:Recipe):Graph{
 const nodes=recipe.nodes.map(node=>({...node}));
 return {version:1,nodes,edges:nodes.slice(0,-1).map((node,index)=>({from:node.id,to:nodes[index+1].id}))};
}

export default function AgentWorkflowRecipes({disabled,currentNodeCount,onApply}:Props){
 const[busy,setBusy]=useState("");const[error,setError]=useState("");
 const apply=async(recipe:Recipe)=>{if(currentNodeCount>0&&!window.confirm(`Заменить текущую карту на схему «${recipe.name}»? Предыдущая версия останется в истории версий.`))return;setBusy(recipe.slug);setError("");try{await onApply(graphFor(recipe))}catch(e){setError(e instanceof Error?e.message:"Не удалось применить схему")}finally{setBusy("")}};
 return <div style={{marginBottom:16}}>
  <div style={{fontSize:12,fontWeight:700,opacity:.55,marginBottom:8}}>ГОТОВЫЕ СХЕМЫ · 1 КЛИК</div>
  <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(210px,1fr))",gap:8}}>{recipes.map(recipe=><button type="button" key={recipe.slug} disabled={disabled||!!busy} onClick={()=>void apply(recipe)} style={{textAlign:"left",padding:12,border:"1px solid #ddd",borderRadius:12,background:"transparent",cursor:"pointer"}}><strong style={{display:"block",marginBottom:4}}>{busy===recipe.slug?"Применяем…":recipe.name}</strong><span style={{fontSize:12,opacity:.62,lineHeight:1.4}}>{recipe.description}</span></button>)}</div>
  {error&&<div style={{marginTop:8,fontSize:13,color:"#b33140"}}>{error}</div>}
 </div>;
}
