import type {ReactNode} from "react";
import RunHandoffPanel from "./RunHandoffPanel";
import RunRecoveryBar from "./RunRecoveryBar";

export default function AgentRunLayout({children}:{children:ReactNode}){
 return <>
  <RunRecoveryBar/>
  <RunHandoffPanel/>
  {children}
 </>;
}
