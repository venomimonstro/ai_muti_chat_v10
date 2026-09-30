"use client";

import styles from "./DevPlanPanel.module.css";

type PlanRow={id?:string;title?:string;role?:string;depends_on?:string[];acceptance?:string;state?:string};
type Step={node_id?:string;title?:string;state?:string};
type Props={plan:Array<Record<string,unknown>>;runState:string;steps?:Step[]};

const labels:Record<string,string>={pending:"Ожидает",running:"В работе",completed:"Готово",failed:"Ошибка",skipped:"Пропущено",conditional:"По необходимости"};

function normalize(row:Record<string,unknown>):PlanRow{
 return {id:String(row.id||""),title:String(row.title||row.id||"Задача"),role:String(row.role||"AI Team"),depends_on:Array.isArray(row.depends_on)?row.depends_on.map(String):[],acceptance:String(row.acceptance||""),state:String(row.state||"pending")};
}

function stepState(item:PlanRow,steps:Step[]){
 if(!item.id)return "";
 const direct=steps.find(step=>step.node_id===`dev-task-${item.id}`);
 if(direct?.state)return direct.state;
 if(item.id==="director"){
  const director=steps.find(step=>String(step.title||"").startsWith("Engineering Director"));
  if(director?.state)return director.state;
 }
 if(item.id==="sandbox-write"){
  const write=steps.find(step=>step.node_id==="approved-github-write");
  if(write?.state)return write.state;
 }
 if(item.id==="final"){
  const review=[...steps].reverse().find(step=>String(step.title||"").startsWith("Final Review"));
  if(review?.state)return review.state;
 }
 return "";
}

function effectiveState(item:PlanRow,index:number,runState:string,steps:Step[]){
 const observed=stepState(item,steps);
 if(observed)return observed;
 if(item.state&&item.state!=="pending")return item.state;
 if(runState==="completed")return item.state==="conditional"?"conditional":"completed";
 if(index===0&&["planning","running","reviewing"].includes(runState))return "running";
 return item.state||"pending";
}

function stateClass(state:string){
 if(state==="completed")return styles.completed;
 if(state==="running")return styles.running;
 if(state==="failed")return styles.failed;
 if(state==="conditional")return styles.conditional;
 return "";
}

export default function DevPlanPanel({plan,runState,steps=[]}:Props){
 const rows=plan.map(normalize).filter(item=>item.id||item.title);
 if(!rows.length)return null;
 const completed=rows.reduce((count,item,index)=>count+(effectiveState(item,index,runState,steps)==="completed"?1:0),0);
 return <section className={styles.panel} aria-label="План Engineering Director">
  <div className={styles.head}>
   <div><span>ENGINEERING DIRECTOR</span><h2>План разработки</h2></div>
   <small>{completed}/{rows.length} этапов</small>
  </div>
  <div className={styles.rows}>
   {rows.map((item,index)=>{const state=effectiveState(item,index,runState,steps);return <article className={`${styles.row} ${stateClass(state)}`} key={item.id||`${item.title}-${index}`}>
    <div className={styles.marker}><span>{index+1}</span></div>
    <div className={styles.body}>
     <div className={styles.title}><strong>{item.title}</strong><em>{labels[state]||state}</em></div>
     <div className={styles.meta}><span>{item.role}</span>{item.depends_on?.length?<span>После: {item.depends_on.join(", ")}</span>:<span>Без зависимостей</span>}</div>
     {item.acceptance&&<p><b>Готово, когда:</b> {item.acceptance}</p>}
    </div>
   </article>})}
  </div>
 </section>;
}
