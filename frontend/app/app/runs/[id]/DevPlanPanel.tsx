"use client";

type PlanRow={
 id?:string;
 title?:string;
 role?:string;
 depends_on?:string[];
 acceptance?:string;
 state?:string;
};

type Props={plan:Array<Record<string,unknown>>;runState:string};

const labels:Record<string,string>={
 pending:"Ожидает",
 running:"В работе",
 completed:"Готово",
 failed:"Ошибка",
 skipped:"Пропущено",
 conditional:"По необходимости",
};

function normalize(row:Record<string,unknown>):PlanRow{
 return {
  id:String(row.id||""),
  title:String(row.title||row.id||"Задача"),
  role:String(row.role||"AI Team"),
  depends_on:Array.isArray(row.depends_on)?row.depends_on.map(String):[],
  acceptance:String(row.acceptance||""),
  state:String(row.state||"pending"),
 };
}

function effectiveState(item:PlanRow,index:number,runState:string){
 if(item.state&&item.state!=="pending")return item.state;
 if(runState==="completed")return item.state==="conditional"?"conditional":"completed";
 if(["failed","canceled","budget_exceeded"].includes(runState))return item.state||"pending";
 if(index===0&&["planning","running","reviewing"].includes(runState))return "running";
 return item.state||"pending";
}

export default function DevPlanPanel({plan,runState}:Props){
 const rows=plan.map(normalize).filter(item=>item.id||item.title);
 if(!rows.length)return null;
 return <section className="devPlanPanel" aria-label="План Engineering Director">
  <div className="devPlanHead">
   <div><span>ENGINEERING DIRECTOR</span><h2>План разработки</h2></div>
   <small>{rows.filter(item=>effectiveState(item,rows.indexOf(item),runState)==="completed").length}/{rows.length} этапов</small>
  </div>
  <div className="devPlanRows">
   {rows.map((item,index)=>{const state=effectiveState(item,index,runState);return <article className={`devPlanRow state-${state}`} key={item.id||`${item.title}-${index}`}>
    <div className="devPlanMarker"><span>{index+1}</span></div>
    <div className="devPlanBody">
     <div className="devPlanTitle"><strong>{item.title}</strong><em>{labels[state]||state}</em></div>
     <div className="devPlanMeta"><span>{item.role}</span>{item.depends_on?.length?<span>После: {item.depends_on.join(", ")}</span>:<span>Без зависимостей</span>}</div>
     {item.acceptance&&<p><b>Готово, когда:</b> {item.acceptance}</p>}
    </div>
   </article>})}
  </div>
 </section>;
}
