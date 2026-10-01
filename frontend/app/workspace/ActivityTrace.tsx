"use client";

import styles from "./ActivityTrace.module.css";

export type ActivityStep={step:string;state:string;message:string;source_count?:number};

function marker(state:string){
 if(state==="completed")return "✓";
 if(state==="warning")return "!";
 if(state==="failed")return "×";
 return "";
}

function rows(steps:ActivityStep[]){return <div className={styles.list}>{steps.map((item,index)=><div key={`${item.step}:${index}`} className={`${styles.row} ${styles[item.state]??""}`}><span className={styles.icon}>{marker(item.state)}</span><span>{item.message}</span></div>)}</div>}

export function ActivityTrace({steps,running}:{steps:ActivityStep[];running:boolean}){
 if(!steps.length)return null;
 if(!running)return <details className={styles.done}><summary>Действия при подготовке ответа · {steps.length}</summary>{rows(steps)}</details>;
 return <section className={styles.wrap} aria-live="polite" aria-label="Ход подготовки ответа"><div className={styles.head}><span className={styles.pulse}/>Что происходит сейчас</div>{rows(steps)}</section>;
}
