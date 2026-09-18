"use client";

import {Component,ReactNode} from "react";

type Props={children:ReactNode;fallback?:ReactNode};type State={failed:boolean};
export class ErrorBoundary extends Component<Props,State>{state:State={failed:false};static getDerivedStateFromError(){return{failed:true}}componentDidCatch(error:Error){console.error("Workspace component failed",error)}render(){if(this.state.failed)return this.props.fallback??<div className="localFailure"><b>Не удалось показать этот блок.</b><button onClick={()=>this.setState({failed:false})}>Повторить</button></div>;return this.props.children}}
