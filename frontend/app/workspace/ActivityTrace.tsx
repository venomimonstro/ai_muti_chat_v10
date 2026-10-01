"use client";

import styles from "./ActivityTrace.module.css";

export type ActivityStep={step:string;state:string;message:string;source_count?:number};

export function ActivityTrace({steps,running}:{steps:ActivityStep[];running:boolean}){
 if(!steps.length)return null;
 const last=steps[steps.length-1];
 const failed=steps.some(item=>item.state==="failed");
 const label=running?last.message:failed?"Не удалось завершить ответ":"Подробности ответа";
 return <details className={`${styles.trace} ${running?styles.active:""}`}>
  <summary><span className={running?styles.pulse:styles.marker} aria-hidden="true">{running?"":failed?"!":"✓"}</span><span role={running?"status":undefined}>{label}</span><span className={styles.chevron} aria-hidden="true">⌄</span></summary>
  <div className={styles.list}>{steps.map((item,index)=><div key={`${item.step}:${index}`} className={styles.row}><span aria-hidden="true">{item.state==="completed"?"✓":item.state==="failed"?"×":"·"}</span><span>{item.message}</span></div>)}</div>
 </details>;
}
