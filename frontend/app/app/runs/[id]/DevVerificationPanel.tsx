"use client";

import styles from "./DevVerificationPanel.module.css";

type Check={ok?:boolean;command?:string;returncode?:number;output?:string;error?:string};
type Change={path:string;operation:string;reason?:string;risk_flags?:string[]};
type Sandbox={ok?:boolean;command?:string;checks?:Check[];output?:string;evidenceLevel?:string;snapshotComplete?:boolean;repositoryEvidenceLevel?:string};

type Props={
 branch?:string;
 workspaceId?:string;
 sandbox?:Record<string,unknown>;
 appliedChanges?:Change[];
};

const evidenceLabel:Record<string,string>={
 structural:"Структурная проверка",
 syntax_bounded_snapshot:"Синтаксис · bounded snapshot",
 syntax_complete_snapshot:"Синтаксис · полный snapshot",
};
const riskLabel:Record<string,string>={
 dependency_manifest:"зависимости",
 deployment:"deployment",
 database_migration:"миграция БД",
 security_or_money_sensitive:"security/деньги",
 destructive_delete:"удаление",
};

function toSandbox(raw?:Record<string,unknown>):Sandbox|null{
 if(!raw)return null;
 const checks=Array.isArray(raw.checks)?raw.checks.filter((item):item is Record<string,unknown>=>!!item&&typeof item==="object").map(item=>({ok:Boolean(item.ok),command:String(item.command||"check"),returncode:typeof item.returncode==="number"?item.returncode:undefined,output:String(item.output||""),error:String(item.error||"")})):[];
 return {
  ok:raw.ok===undefined?undefined:Boolean(raw.ok),
  command:String(raw.command||""),
  checks,
  output:String(raw.output||""),
  evidenceLevel:String(raw.evidence_level||""),
  snapshotComplete:Boolean(raw.snapshot_complete),
  repositoryEvidenceLevel:String(raw.repository_evidence_level||""),
 };
}

export default function DevVerificationPanel({branch,workspaceId,sandbox:rawSandbox,appliedChanges=[]}:Props){
 const sandbox=toSandbox(rawSandbox);
 const checks=sandbox?.checks||[];
 if(!branch&&!workspaceId&&!sandbox&&!appliedChanges.length)return null;
 const evidence=sandbox?.evidenceLevel?evidenceLabel[sandbox.evidenceLevel]||sandbox.evidenceLevel:"";
 return <section className={styles.panel} aria-label="Проверка Dev Studio">
  <div className={styles.head}><div><span>VERIFICATION</span><h2>Доказательства результата</h2></div>{sandbox&&<strong className={sandbox.ok===false?styles.failed:styles.passed}>{sandbox.ok===false?"FAILED":"PASSED"}</strong>}</div>
  <div className={styles.meta}>{branch&&<div><small>Рабочая ветка</small><code>{branch}</code></div>}{workspaceId&&<div><small>Workspace</small><code>{workspaceId}</code></div>}{appliedChanges.length>0&&<div><small>Изменено файлов</small><b>{appliedChanges.length}</b></div>}{evidence&&<div><small>Уровень доказательства</small><b>{evidence}</b></div>}</div>
  {sandbox&&sandbox.evidenceLevel==="syntax_bounded_snapshot"&&<div className={styles.structural}><span>!</span><div><strong>Проверена не вся кодовая база</strong><p>Syntax checks выполнены на bounded snapshot. Это не равно полному project test suite; Dev Studio не выдаёт такой PASS за полную проверку проекта.</p></div></div>}
  {checks.length>0&&<div className={styles.checks}>{checks.map((check,index)=><details className={check.ok?styles.checkPassed:styles.checkFailed} key={`${check.command}-${index}`} open={!check.ok}><summary><span>{check.ok?"✓":"×"}</span><strong>{check.command}</strong><em>{check.ok?"PASS":"FAIL"}</em></summary>{(check.output||check.error)&&<pre>{(check.output||check.error||"").slice(-8000)}</pre>}</details>)}</div>}
  {!checks.length&&sandbox?.command&&<div className={styles.structural}><span>✓</span><div><strong>{sandbox.command}</strong><p>{sandbox.output||"Проверка завершена."}</p></div></div>}
  {appliedChanges.length>0&&<div className={styles.files}><h3>Изменения</h3>{appliedChanges.map((change,index)=><div className={styles.file} key={`${change.path}-${index}`}><code>{change.path}</code><span>{change.operation}{change.risk_flags?.length?` · ${change.risk_flags.map(flag=>riskLabel[flag]||flag).join(", ")}`:""}</span></div>)}</div>}
 </section>;
}
