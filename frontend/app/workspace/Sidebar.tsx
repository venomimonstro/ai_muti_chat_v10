"use client";

import Link from "next/link";
import {useEffect,useMemo,useState} from "react";
import {api} from "../../lib/api";
import type {Wallet} from "../../lib/types";
import {Icon} from "./Icons";
import type {ConversationFolder,ConversationSummary} from "./types";

type Props={
 items:ConversationSummary[];folders:ConversationFolder[];activeId:string|null;wallet:Wallet|null;collapsed:boolean;runningIds?:Set<string>;
 onToggle:()=>void;onCreate:()=>void;onSelect:(id:string)=>void;onSearch:()=>void;
 onCreateFolder:(name:string)=>Promise<void>;onRenameFolder:(id:string,name:string)=>Promise<void>;onPinFolder:(id:string,pinned:boolean)=>Promise<void>;onDeleteFolder:(id:string)=>Promise<void>;
 onRenameChat:(id:string,title:string)=>Promise<void>;onPinChat:(id:string,pinned:boolean)=>Promise<void>;onMoveChat:(id:string,folder:string|null)=>Promise<void>;onDeleteChat:(id:string)=>Promise<void>;
};

export function Sidebar(props:Props){
 const {items,folders,activeId,wallet,collapsed}=props;const runningIds=props.runningIds??new Set<string>();
 const [openMenu,setOpenMenu]=useState<string|null>(null);const [folderInput,setFolderInput]=useState(false);const [folderName,setFolderName]=useState("");const[profileOpen,setProfileOpen]=useState(false);
 useEffect(()=>{if(window.innerWidth<=820&&localStorage.getItem("aiws:sidebar-collapsed")===null&&!collapsed)props.onToggle();},[]);
 const pinned=useMemo(()=>items.filter(x=>x.is_pinned),[items]);
 const unfiled=useMemo(()=>items.filter(x=>!x.folder&&!x.is_pinned),[items]);
 const byFolder=(id:string)=>items.filter(x=>x.folder===id&&!x.is_pinned);
 const askRename=async(id:string,current:string)=>{const value=window.prompt("Новое название",current)?.trim();if(value&&value!==current)await props.onRenameChat(id,value);setOpenMenu(null)};
 const askFolderRename=async(id:string,current:string)=>{const value=window.prompt("Название папки",current)?.trim();if(value&&value!==current)await props.onRenameFolder(id,value);setOpenMenu(null)};
 const createFolder=async()=>{const name=folderName.trim();if(!name)return;await props.onCreateFolder(name);setFolderName("");setFolderInput(false)};
 const logout=async()=>{try{await api("/auth/logout/",{method:"POST"});}finally{window.location.assign("/login")}};
 const ChatRow=({item}:{item:ConversationSummary})=>{const running=runningIds.has(item.id);return <div className={`sideRow ${item.id===activeId?"active":""} ${running?"running":""}`}><button className="sideRowMain" onClick={()=>props.onSelect(item.id)} title={running?`${item.title} · ответ формируется`:item.title}>{item.is_pinned&&<Icon name="pin" size={13}/>}<span>{item.title||"Новый чат"}</span></button><button className="sideMore" aria-label="Меню чата" onClick={()=>setOpenMenu(openMenu===`chat:${item.id}`?null:`chat:${item.id}`)}><Icon name="more"/></button>{openMenu===`chat:${item.id}`&&<div className="contextMenu"><button onClick={()=>void askRename(item.id,item.title)}><Icon name="pencil"/>Переименовать</button><button onClick={()=>void props.onPinChat(item.id,!item.is_pinned).then(()=>setOpenMenu(null))}><Icon name="pin"/>{item.is_pinned?"Открепить":"Закрепить"}</button><div className="contextSubLabel">Папка</div><button onClick={()=>void props.onMoveChat(item.id,null).then(()=>setOpenMenu(null))}><Icon name="folder"/>Без папки</button>{folders.map(folder=><button key={folder.id} onClick={()=>void props.onMoveChat(item.id,folder.id).then(()=>setOpenMenu(null))}><Icon name="folder"/>{folder.name}</button>)}<div className="contextDivider"/><button className="danger" disabled={running} title={running?"Сначала остановите или дождитесь ответа":"Удалить чат"} onClick={()=>void props.onDeleteChat(item.id).then(()=>setOpenMenu(null))}><Icon name="trash"/>{running?"Сначала остановите ответ":"Удалить"}</button></div>}</div>};
 return <aside className={`workspaceSidebar ${collapsed?"collapsed":""}`}>
  <div className="sideTop"><button className="iconButton" onClick={props.onToggle} aria-label={collapsed?"Развернуть панель":"Скрыть панель"}><Icon name="panel"/></button>{!collapsed&&<Link className="workspaceBrand" href="/">AI Workspace</Link>}</div>
  <button className="newChatButton" onClick={props.onCreate}><Icon name="plus"/><span>Новый чат</span></button>
  <button className="sideAction" onClick={props.onSearch}><Icon name="search"/><span>Поиск</span><kbd>⌘K</kbd></button>
  {!collapsed&&<div className="sideScroll">
   {pinned.length>0&&<section className="sideSection"><div className="sideLabel">Закреплённые</div>{pinned.map(item=><ChatRow key={item.id} item={item}/>)}</section>}
   <section className="sideSection"><div className="sideLabel sideLabelAction"><span>Папки</span><button aria-label="Создать папку" onClick={()=>setFolderInput(true)}><Icon name="folderPlus" size={16}/></button></div>{folderInput&&<div className="folderInput"><input autoFocus value={folderName} placeholder="Название папки" onChange={e=>setFolderName(e.target.value)} onKeyDown={e=>{if(e.key==="Enter")void createFolder();if(e.key==="Escape")setFolderInput(false)}}/><button onClick={()=>void createFolder()}><Icon name="check"/></button></div>}{folders.map(folder=><div key={folder.id} className="folderBlock"><div className="sideRow"><button className="sideRowMain"><Icon name="folder" size={15}/><span>{folder.name}</span><small>{folder.conversation_count}</small></button><button className="sideMore" onClick={()=>setOpenMenu(openMenu===`folder:${folder.id}`?null:`folder:${folder.id}`)}><Icon name="more"/></button>{openMenu===`folder:${folder.id}`&&<div className="contextMenu"><button onClick={()=>void askFolderRename(folder.id,folder.name)}><Icon name="pencil"/>Переименовать</button><button onClick={()=>void props.onPinFolder(folder.id,!folder.is_pinned).then(()=>setOpenMenu(null))}><Icon name="pin"/>{folder.is_pinned?"Открепить":"Закрепить"}</button><button className="danger" onClick={()=>void props.onDeleteFolder(folder.id).then(()=>setOpenMenu(null))}><Icon name="trash"/>Удалить папку</button></div>}</div>{byFolder(folder.id).map(item=><ChatRow key={item.id} item={item}/>)}</div>)}</section>
   <section className="sideSection"><div className="sideLabel">Чаты</div>{unfiled.map(item=><ChatRow key={item.id} item={item}/>)}</section>
  </div>}
  <div className="sideFooter">{!collapsed&&<Link className="walletMini" href="/app/wallet"><span><Icon name="wallet"/>Баланс</span><b>{Number(wallet?.available_rub??0).toFixed(2).replace(".",",")} ₽</b></Link>}<div className="profileRow"><button type="button" className="profileButton" onClick={()=>setProfileOpen(current=>!current)} aria-expanded={profileOpen}><span className="avatar"><Icon name="user" size={16}/></span>{!collapsed&&<span>Профиль</span>}</button>{!collapsed&&<Link className="iconButton" href="/app/settings" aria-label="Настройки"><Icon name="settings"/></Link>}{profileOpen&&<div className="profileMenu"><Link href="/app/compare"><Icon name="scale"/>Сравнение моделей</Link><Link href="/app/images"><Icon name="spark"/>Изображения</Link><Link href="/app/development"><Icon name="folder"/>Разработка · GitHub</Link><div className="contextDivider"/><Link href="/app/account"><Icon name="user"/>Аккаунт</Link><Link href="/app/notifications"><Icon name="bell"/>Уведомления</Link><Link href="/app/projects"><Icon name="folder"/>Проекты</Link><Link href="/app/usage"><Icon name="wallet"/>Использование</Link><Link href="/app/wallet"><Icon name="wallet"/>Баланс и платежи</Link><Link href="/app/settings"><Icon name="settings"/>Настройки</Link><Link href="/app/help"><Icon name="search"/>Помощь</Link><div className="contextDivider"/><button type="button" onClick={()=>void logout()}>Выйти</button></div>}</div></div>
 </aside>;
}
