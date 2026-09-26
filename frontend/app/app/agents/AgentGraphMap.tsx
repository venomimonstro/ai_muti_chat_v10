"use client";

type Node={id:string;title:string;type:string;on_true?:string;on_false?:string;status?:string;wait_minutes?:number};
type Props={nodes:Node[]};

const labels:Record<string,string>={
 llm:"AI-задача",research:"Исследование",web:"Интернет",files:"Файлы",image:"Изображение",review:"Проверка",
 analytics:"Аналитика",condition:"Условие",approval:"Подтверждение",wait:"Ожидание",notify:"Уведомление",publish:"Публикация",finish:"Финиш",
};

export default function AgentGraphMap({nodes}:Props){
 const byId=new Map(nodes.map(node=>[node.id,node]));
 if(!nodes.length)return <div style={{padding:22,border:"1px dashed #bbb",borderRadius:14,textAlign:"center",opacity:.62}}>Карта пока пустая.</div>;
 return <div style={{overflowX:"auto",padding:"4px 2px 10px"}}>
  <div style={{display:"grid",gap:0,minWidth:420,maxWidth:760,margin:"0 auto"}}>
   {nodes.map((node,index)=>{
    const next=nodes[index+1];
    const yes=node.on_true?byId.get(node.on_true):next;
    const no=node.on_false?byId.get(node.on_false):next;
    const isCondition=node.type==="condition";
    return <div key={node.id} style={{display:"grid",justifyItems:"center"}}>
     <div style={{width:"min(100%,560px)",border:"1px solid #d7d7d7",borderRadius:16,padding:"13px 15px",background:"var(--panel,transparent)",boxSizing:"border-box"}}>
      <div style={{display:"flex",justifyContent:"space-between",gap:12,alignItems:"center"}}><strong style={{fontSize:15}}>{index+1}. {node.title||`Шаг ${index+1}`}</strong><span style={{fontSize:11,padding:"4px 7px",border:"1px solid #ddd",borderRadius:999,whiteSpace:"nowrap"}}>{labels[node.type]||node.type}</span></div>
      {node.type==="wait"&&<div style={{fontSize:12,opacity:.6,marginTop:6}}>Пауза: {node.wait_minutes||60} мин.</div>}
      {node.type==="publish"&&<div style={{fontSize:12,opacity:.6,marginTop:6}}>{node.status==="publish"?"Публичная публикация":"Сохранение черновика"}</div>}
      {isCondition&&<div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:8,marginTop:10}}><div style={{border:"1px solid #cfd8cf",borderRadius:10,padding:"8px 9px",fontSize:12}}><strong>Да →</strong> {yes?.title||"Следующий шаг"}</div><div style={{border:"1px solid #ddd1d1",borderRadius:10,padding:"8px 9px",fontSize:12}}><strong>Нет →</strong> {no?.title||"Следующий шаг"}</div></div>}
     </div>
     {index<nodes.length-1&&node.type!=="finish"&&<div aria-hidden="true" style={{height:28,width:1,background:"#c9c9c9",position:"relative"}}><span style={{position:"absolute",bottom:-3,left:-4,fontSize:12,opacity:.55}}>↓</span></div>}
    </div>;
   })}
  </div>
 </div>;
}
