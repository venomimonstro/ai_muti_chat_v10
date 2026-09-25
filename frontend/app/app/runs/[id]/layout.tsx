import type {ReactNode} from "react";
import RunRecoveryBar from "./RunRecoveryBar";

export default function AgentRunLayout({children}:{children:ReactNode}){
 return <>
  <RunRecoveryBar/>
  {children}
 </>;
}
