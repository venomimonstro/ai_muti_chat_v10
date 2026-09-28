"use client";

import {FormEvent,useCallback,useEffect,useMemo,useState} from "react";
import {api} from "../../lib/api";
import type {ImageGeneration,ImageModel} from "../../lib/types";
import {Icon} from "./Icons";

type Preview={model:string;count:number;expected_cost_rub:string;confirmation_required:boolean;confirmation_threshold_rub:string};

type Props={
 open:boolean;
 onClose:()=>void;
 conversationId?:string|null;
 ensureConversation?:()=>Promise<string|null>;
};

const errorLabel:Record<string,string>={
 blank_image:"Провайдер вернул пустое изображение. Средства не списаны.",
 invalid_image:"Провайдер вернул повреждённое изображение. Средства не списаны.",
 invalid_response:"OpenAI вернул некорректный результат. Средства не списаны.",
 image_count_mismatch:"OpenAI вернул другое количество изображений. Операция отменена, средства не списаны.",
 timeout:"OpenAI не успел ответить. Средства не списаны.",
 upstream_down:"OpenAI Images временно недоступен. Средства не списаны.",
 queue_unavailable:"Очередь генерации временно недоступна. Средства не списаны.",
 internal_error:"Не удалось завершить генерацию. Средства за неготовый результат не списаны.",
};

const rub=(value:string|number)=>{
 const number=Number(value||0);
 return Number.isFinite(number)?`${number.toLocaleString("ru-RU",{minimumFractionDigits:2,maximumFractionDigits:2})} ₽`:`${value} ₽`;
};

export function ChatImageStudio({open,onClose,conversationId,ensureConversation}:Props){
 const[models,setModels]=useState<ImageModel[]>([]);const[model,setModel]=useState("");const[prompt,setPrompt]=useState("");const[size,setSize]=useState("");const[quality,setQuality]=useState("");const[count,setCount]=useState(1);const[preview,setPreview]=useState<Preview|null>(null);const[generation,setGeneration]=useState<ImageGeneration|null>(null);const[busy,setBusy]=useState(false);const[error,setError]=useState("");
 const selected=useMemo(()=>models.find(item=>item.slug===model)??null,[models,model]);
 const pending=generation?.state==="queued"||generation?.state==="running";
 const loadGeneration=useCallback(async(id:string)=>{try{setGeneration(await api<ImageGeneration>(`/images/generations/${id}/`))}catch{}},[]);
 useEffect(()=>{if(!open)return;let active=true;setError("");void api<ImageModel[]>("/image-models/").then(rows=>{if(!active)return;setModels(rows);setModel(current=>rows.some(item=>item.slug===current)?current:(rows[0]?.slug??""))}).catch(reason=>{if(active)setError(reason instanceof Error?reason.message:"Не удалось загрузить модели изображений")});return()=>{active=false}},[open]);
 useEffect(()=>{if(selected){if(!selected.supported_sizes.includes(size))setSize(selected.supported_sizes[0]??"1024x1024");if(!selected.supported_qualities.includes(quality))setQuality(selected.supported_qualities[0]??"standard");if(count>selected.max_images)setCount(selected.max_images)}},[selected,size,quality,count]);
 useEffect(()=>{if(!pending||!generation)return;const timer=window.setTimeout(()=>void loadGeneration(generation.id),document.visibilityState==="hidden"?4500:1800);return()=>window.clearTimeout(timer)},[pending,generation,loadGeneration]);
 const basePayload=()=>({model,prompt:prompt.trim(),size,quality,count});
 const resolveConversation=async()=>conversationId??await ensureConversation?.()??null;
 const create=async(confirmed:boolean)=>{setBusy(true);setError("");try{const conversation=await resolveConversation();const created=await api<ImageGeneration>("/images/generations/",{method:"POST",headers:{"Idempotency-Key":`chat-image:${crypto.randomUUID()}`},body:JSON.stringify({...basePayload(),...(conversation?{conversation}:{}),confirm_cost:confirmed})});setGeneration(created);setPreview(null);if(created.state==="completed")setPrompt("")}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось создать изображение")}finally{setBusy(false)}};
 const submit=async(event:FormEvent)=>{event.preventDefault();if(!prompt.trim()||!model||busy)return;setBusy(true);setError("");try{const value=await api<Preview>("/images/preview/",{method:"POST",body:JSON.stringify(basePayload())});setPreview(value);if(!value.confirmation_required){setBusy(false);await create(false);return}}catch(reason){setError(reason instanceof Error?reason.message:"Не удалось рассчитать стоимость")}finally{setBusy(false)}};
 if(!open)return null;
 return <div className="imageStudioBackdrop" role="presentation" onMouseDown={event=>{if(event.target===event.currentTarget&&!busy)onClose()}}><section className="imageStudioDialog" role="dialog" aria-modal="true" aria-label="Создать изображение"><header><div><span>OPENAI IMAGES</span><h2>Создать изображение</h2><p>Опишите результат обычными словами. Изображение сохранится в истории и будет связано с этим чатом.</p></div><button className="iconButton" type="button" onClick={onClose} aria-label="Закрыть"><Icon name="x"/></button></header>{error&&<div className="imageStudioError">{error}</div>}{models.length===0&&!error?<div className="imageStudioEmpty">Подключённая image-модель не найдена.</div>:<form onSubmit={submit}><textarea rows={4} maxLength={4000} value={prompt} onChange={event=>{setPrompt(event.target.value);setPreview(null)}} placeholder="Например: дорогой рекламный креатив, светлый фон, реалистичный продукт крупным планом…" autoFocus/><div className="imageStudioOptions"><label>Модель<select value={model} onChange={event=>{setModel(event.target.value);setPreview(null)}}>{models.map(item=><option key={item.slug} value={item.slug}>{item.display_name}</option>)}</select></label><label>Размер<select value={size} onChange={event=>{setSize(event.target.value);setPreview(null)}}>{selected?.supported_sizes.map(item=><option key={item}>{item}</option>)}</select></label><label>Качество<select value={quality} onChange={event=>{setQuality(event.target.value);setPreview(null)}}>{selected?.supported_qualities.map(item=><option key={item}>{item}</option>)}</select></label><label>Кол-во<input type="number" min={1} max={Math.min(selected?.max_images??1,4)} value={count} onChange={event=>{setCount(Math.max(1,Math.min(Number(event.target.value)||1,Math.min(selected?.max_images??1,4))));setPreview(null)}}/></label></div>{preview&&<div className="imageStudioCost"><span>Максимальный резерв</span><b>{rub(preview.expected_cost_rub)}</b>{preview.confirmation_required&&<button type="button" disabled={busy} onClick={()=>void create(true)}>Подтвердить стоимость и создать</button>}</div>}<footer><small>Списывается только подтверждённый успешный результат. При ошибке провайдера резерв возвращается.</small><button className="imageStudioPrimary" disabled={busy||!prompt.trim()||!model}>{busy?"Запускаем…":preview?.confirmation_required?"Пересчитать":"Создать"}</button></footer></form>}{generation&&<div className="imageStudioResult"><div className="imageStudioResultHead"><b>{generation.state==="completed"?"Готово":generation.state==="failed"?"Не удалось":"OpenAI создаёт изображение…"}</b><span>{generation.actual_cost_rub!==null?rub(generation.actual_cost_rub):`резерв до ${rub(generation.estimated_cost_rub)}`}</span></div>{generation.error_code&&<div className="imageStudioError">{errorLabel[generation.error_code]??"Генерация завершилась ошибкой. Средства за неготовый результат не списаны."}</div>}{generation.images.length>0&&<div className="imageStudioGrid">{generation.images.map(image=><figure key={image.id}><img src={image.source_url} alt={image.revised_prompt||generation.prompt}/>{image.revised_prompt&&<figcaption>{image.revised_prompt}</figcaption>}</figure>)}</div>}</div>}</section></div>;
}
