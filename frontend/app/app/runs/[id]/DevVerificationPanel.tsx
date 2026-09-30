"use client";

import styles from "./DevVerificationPanel.module.css";

type Check={ok?:boolean;command?:string;returncode?:number;output?:string;error?:string};
type Change={path:string;operation:string;reason?:string};
type Sandbox={ok?:boolean;command?:string;checks?:Check[];output?:string};

type Props={
 branch?:string;
 workspaceId?:string;
 sandbox?:Record<string,unknown>;
 appliedChanges?:Change[];
};

function toSandbox(raw?:Record<string,unknown>):Sandbox|null{
 if(!raw)return null;
 const checks=Array.isArray(raw.checks)?raw.checks.filter((item):item is Record<string,unknown>=>!!item&&typeof item==="object").map(item=>({ok:Boolean(item.ok),command:String(item.command||"check"),returncode:typeof item.returncode==="number"?item.returncode:undefined,output:String(item.output||""),error:String(item.error||"")})):[];
 return {ok:raw.ok===undefined?undefined:Boolean(raw.ok),command:String(raw.command||""),checks,output:String(raw.output||"")};
}

export default function DevVerificationPanel({branch,workspaceId,sandbox:rawSandbox,appliedChanges=[]}:Props){
 const sandbox=toSandbox(rawSandbox);
 const checks=sandbox?.checks||[];
 if(!branch&&!workspaceId&&!sandbox&&!appliedChanges.length)return null;
 return <section className={styles.panel} aria-label="Проверка Dev Studio">
  <div className={styles.head}><div><span>VERIFICATION</span><h2>Доказательства результата</h2></div>{sandbox&&<strong className={sandbox.ok===false?styles.failed:styles.passed}>{sandbox.ok===false?"FAILED":"PASSED"}</strong>}</div>
  <div className={styles.meta}>{branch&&<div><small>Рабочая ветка</small><code>{branch}</code></div>}{workspaceId&&<div><small>Workspace</small><code>{workspaceId}</code></div>}{appliedChanges.length>0&&<div><small>Изменено файлов</small><b>{appliedChanges.length}</b></div>}</div>
  {checks.length>0&&<div className={styles.checks}>{checks.map((check,index)=><details className={check.ok?styles.checkPassed:styles.checkFailed} key={`${check.command}-${index}`} open={!check.ok}><summary><span>{check.ok?"✓":"×"}</span><strong>{check.command}</strong><em>{check.ok?"PASS":"FAIL"}</em></summary>{(check.output||check.error)&&<pre>{(check.output||check.error||"").slice(-8000)}</pre>}</details>)}</div>}
  {!checks.length&&sandbox?.command&&<div className={styles.structural}><span>✓</span><div><strong>{sandbox.command}</strong><p>{sandbox.output||"Проверка завершена."}</p></div></div>}
  {appliedChanges.length>0&&<div className={styles.files}><h3>Изменения</h3>{appliedChanges.map((change,index)=><div className={styles.file} key={`${change.path}-${index}`}><code>{change.path}</code><span>{change.operation}</span></div>)}</div>}
 </section>;
}
