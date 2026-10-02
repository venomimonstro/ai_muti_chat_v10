import assert from "node:assert/strict";
import {mkdir} from "node:fs/promises";
import {chromium} from "playwright";

const base = process.env.CHAT_UX_BASE_URL ?? "http://127.0.0.1:3000";
const browser = await chromium.launch({headless:true, executablePath:process.env.CHAT_UX_CHROMIUM || undefined,
  args:process.env.CHAT_UX_CHROMIUM ? ["--disable-gpu","--disable-software-rasterizer","--disable-features=Vulkan","--single-process"] : []});
const context = await browser.newContext({viewport:{width:1440,height:960}});
const page = await context.newPage();page.setDefaultTimeout(10000);
const errors=[];page.on("pageerror",error=>errors.push(error.message));
const created="2026-10-01T10:00:00Z";
const message=(id,role,content,status="completed",generation=null)=>({id,role,content,status,generation,created_at:created});
const conversations = new Map(["a","b"].map(id=>[`chat-${id}`,{id:`chat-${id}`,title:id==="a"?"План на неделю":"Идеи для контента",routing_mode:"auto",selected_model:"",project:null,active_branch:null,memory_enabled:true,created_at:created,updated_at:created,messages:[message(`user-${id}`,"user","Помоги составить план запуска проекта."),message(`answer-${id}`,"assistant","## План запуска\n\nНачните с проверки продукта.\n\n1. Проверьте основные сценарии.\n2. Пригласите первых пользователей.\n\n| День | Задача |\n| --- | --- |\n| Понедельник | Проверка продукта |\n| Вторник | Первые пользователи |")]}]));
const drafts=new Map([["chat-a","Черновик чата А"],["chat-b","Черновик чата Б"]]);
let slowUpload=false, slowB=false, requireConfirmation=false, holdStream=false, sends=0, cancellations=0, currentGeneration=null;
const sse=(event,data)=>`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
await page.route("**/api/v1/**",async route=>{
 const request=route.request(), path=new URL(request.url()).pathname, method=request.method();let data={};
 const id=path.match(/(?:conversations|conversation-workspace)\/([^/]+)/)?.[1];
 if(path.endsWith("/auth/me/"))data={id:"user",status:"active",role:"customer",email:"user@example.test"};
 else if(path.endsWith("/auth/csrf/"))data={csrf_token:"test"};
 else if(path.includes("/conversation-summaries/"))data=[...conversations.values()].map(({messages,...item})=>item);
 else if(path.includes("/conversation-workspace/")){if(slowB&&id==="chat-b")await new Promise(r=>setTimeout(r,600));data={conversation:conversations.get(id),has_more:false,next_before:null};}
 else if(path.includes("/draft/")){if(method==="PUT")drafts.set(id,request.postDataJSON().content);if(method==="DELETE")drafts.delete(id);data={content:drafts.get(id)??"",version:1,updated_at:null};}
 else if(path.includes("/wallet/"))data={available_rub:"1248.50",reserved_rub:"0"};
 else if(path.includes("/models/"))data=[{slug:"system-pro",display_name:"System Pro",available:true,provider:"system",capabilities:["text","streaming"]}];
 else if(path.includes("/projects/"))data=[{id:"project-files",name:"Файлы чата",role:"owner",archived_at:null}];
 else if(path.includes("/conversation-folders/"))data=[];
 else if(path.match(/\/conversations\/chat-[ab]\/$/) && method==="PATCH"){Object.assign(conversations.get(id),request.postDataJSON());data=conversations.get(id);}
 else if(path.includes("/files/")){if(slowUpload&&method==="POST")await new Promise(r=>setTimeout(r,700));data={id:"file-1",original_name:"notes.txt",detected_type:"txt",status:method==="POST"?"parsing":"ready",project:"project-files",size_bytes:16};}
 else if(path.includes("/messages/preview/"))data={estimated_min_rub:"0",estimated_max_rub:"12.50",confirmation_required:requireConfirmation,confirmation_threshold_rub:"10"};
 else if(path.includes("/messages/status/"))data={found:false};
 else if(path.includes("/messages/cancel/")){cancellations++;if(currentGeneration){currentGeneration.state="cancelled";const chat=conversations.get(id);const answer=chat.messages.at(-1);answer.status="partial";}data={state:"cancelled"};}
 else if(path.includes("/messages/stream/")){
  sends++;const input=request.postDataJSON();const chat=conversations.get(id);currentGeneration={id:`gen-${sends}`,state:holdStream?"running":"completed",model:"System Pro",provider:"system",cost_rub:holdStream?null:"0.42",input_tokens:12,output_tokens:20};
  chat.messages.push({...message(`sent-${sends}`,"user",input.content,"saved"),client_message_id:input.client_message_id},message(`reply-${sends}`,"assistant","Полезный ответ.",holdStream?"streaming":"completed",currentGeneration));
  const body=sse("generation",{id:currentGeneration.id,state:currentGeneration.state})+sse("delta",{text:"Полезный ответ."})+(holdStream?"":sse("completed",{state:"completed",cost_rub:"0.42"}));
  await route.fulfill({status:200,contentType:"text/event-stream",body});return;
 }
 else if(path.includes("/search/")){const q=new URL(request.url()).searchParams.get("q");if(q==="старый")await new Promise(r=>setTimeout(r,500));data={query:q,results:[{id:q,type:"conversation",title:q,excerpt:"Найденный чат",conversation_id:"chat-a"}]};}
 await route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(data)});
});
const editor=()=>page.getByRole("textbox",{name:"Сообщение",exact:true});
const wait=ms=>new Promise(r=>setTimeout(r,ms));
try{
 await page.goto(`${base}/app`);await editor().waitFor();await page.waitForFunction(()=>document.querySelector('textarea[aria-label="Сообщение"]')?.value==="Черновик чата А");
 assert.equal(drafts.get("chat-a"),"Черновик чата А");
 assert.notEqual(await page.locator(".profileButton").evaluate(node=>getComputedStyle(node).color),"rgb(255, 255, 255)");
 const shell=await page.locator(".composerShell").boundingBox();assert(shell.y+shell.height<=960,"composer must fit viewport");
 await page.locator(".chatTitle button").click();await page.getByRole("dialog",{name:"Переименовать чат",exact:true}).waitFor();assert.equal(await page.getByRole("textbox",{name:"Новое название"}).inputValue(),"План на неделю");await page.keyboard.press("Escape");assert.equal(await page.locator("dialog[open]").count(),0);
 console.log("PASS initial draft hydration, rename dialog and desktop viewport");
 slowB=true;await page.getByRole("button",{name:"Идеи для контента",exact:true}).click();await wait(60);await page.getByRole("button",{name:"План на неделю",exact:true}).first().click();await wait(800);
 assert.equal(await page.locator(".chatTitle").innerText(),"План на неделю");assert.equal(await editor().inputValue(),"Черновик чата А");slowB=false;
 console.log("PASS rapid conversation switch preserves latest choice and its draft");
 await page.getByRole("button",{name:"Поиск",exact:true}).click();const search=page.getByRole("textbox",{name:"Поиск по рабочему пространству"});await search.fill("старый");await wait(300);await search.fill("новый");await page.getByRole("button",{name:/Чат новый/}).waitFor();await wait(550);assert.equal(await page.locator(".searchResult").count(),1);assert.match(await page.locator(".searchResult").innerText(),/новый/);await search.fill("н");await wait(300);assert.equal(await page.locator(".searchResult").count(),0);await page.keyboard.press("Escape");assert.equal(await page.locator("dialog[open]").count(),0);
 await page.getByRole("button",{name:"Поиск и материалы чата",exact:true}).click();await page.getByRole("dialog",{name:"Поиск и материалы чата",exact:true}).waitFor();await page.keyboard.press("Escape");assert.equal(await page.locator("dialog[open]").count(),0);
 console.log("PASS search discards stale responses and Escape closes dialogs");
 requireConfirmation=true;await editor().fill("Проверь бюджет проекта");await page.getByRole("button",{name:"Отправить",exact:true}).click();await page.getByRole("dialog",{name:"Подтвердить расход"}).waitFor();assert.equal(sends,0);await page.getByRole("button",{name:"Отмена",exact:true}).click();await page.waitForFunction(()=>document.querySelector('textarea[aria-label="Сообщение"]')?.value==="Проверь бюджет проекта");assert.equal(sends,0);assert.equal(await page.locator(".topNotice.error").count(),0);
 await page.getByRole("button",{name:"Отправить",exact:true}).click();await page.getByRole("button",{name:"Отправить запрос",exact:true}).click();await page.getByText("Полезный ответ.",{exact:true}).waitFor();await wait(250);assert.equal(sends,1);assert.equal(await page.getByText("Полезный ответ.",{exact:true}).count(),1);
 console.log("PASS cost cancellation preserves prompt; confirmed send renders one answer");
 requireConfirmation=false;
 await editor().fill("Проверь приложенный документ");await page.locator('input[type="file"]').first().setInputFiles({name:"notes.txt",mimeType:"text/plain",buffer:Buffer.from("План проекта")});await page.getByText("Обрабатывается",{exact:true}).waitFor();assert.equal(await page.getByRole("button",{name:"Отправить",exact:true}).isEnabled(),false);await editor().fill("Проверь документ и предложи правки");await page.getByText("Готов",{exact:true}).waitFor();assert.equal(await page.getByRole("button",{name:"Отправить",exact:true}).isEnabled(),true);assert.equal(await editor().inputValue(),"Проверь документ и предложи правки");
 console.log("PASS file extraction gates send while draft remains editable");
 await page.getByRole("button",{name:"Идеи для контента",exact:true}).click();await page.waitForFunction(()=>document.querySelector('.chatTitle')?.textContent==="Идеи для контента");assert.equal(await page.locator(".attachmentChip").count(),0);
 await page.getByRole("button",{name:"План на неделю",exact:true}).first().click();await page.getByText("notes.txt",{exact:true}).waitFor();assert.equal(await editor().inputValue(),"Проверь документ и предложи правки");
 await page.reload();await page.getByText("notes.txt",{exact:true}).waitFor();assert.equal(await page.locator(".attachmentChip").count(),1);
 await page.getByRole("button",{name:"Убрать файл notes.txt",exact:true}).click();await page.reload();await editor().waitFor();await wait(200);assert.equal(await page.locator(".attachmentChip").count(),0);
 drafts.set("chat-b","");await page.evaluate(()=>localStorage.removeItem("aiws:draft:chat-b"));
 slowUpload=true;await page.locator('input[type="file"]').nth(1).setInputFiles({name:"photo.png",mimeType:"image/png",buffer:Buffer.from("image fixture")});await wait(150);
 await page.getByRole("button",{name:"Идеи для контента",exact:true}).click();await page.waitForFunction(()=>document.querySelector('.chatTitle')?.textContent==="Идеи для контента");const otherDraft=await editor().inputValue();assert.equal(otherDraft,"");await wait(850);assert.equal(await editor().inputValue(),otherDraft);assert.equal(await page.locator(".attachmentChip").count(),0);
 slowUpload=false;await page.getByRole("button",{name:"План на неделю",exact:true}).first().click();await page.getByText("notes.txt",{exact:true}).waitFor();
 console.log("PASS attachment selection survives navigation/reload/removal and late image upload stays in its chat");
 holdStream=true;await editor().fill("Длинный ответ");await page.getByRole("button",{name:"Отправить",exact:true}).click();await page.locator(".message.assistant.streaming").filter({hasText:"Полезный ответ."}).waitFor();await page.getByRole("button",{name:"Остановить ответ",exact:true}).click();await wait(300);assert.equal(cancellations,1);
 console.log("PASS live Stop sends one cancellation");
 // Reloading an in-flight persisted response exposes the same Stop action.
 currentGeneration.state="running";conversations.get("chat-a").messages.at(-1).status="streaming";
 await page.reload();await page.getByRole("button",{name:"Остановить ответ",exact:true}).waitFor();await page.getByRole("button",{name:"Остановить ответ",exact:true}).click();await wait(600);assert.equal(cancellations,2);
 console.log("PASS recovered generation uses composer Stop");
 const touch=await context.newCDPSession(page);await touch.send("Emulation.setTouchEmulationEnabled",{enabled:true,maxTouchPoints:1});
 await page.setViewportSize({width:390,height:844});await page.reload();await editor().waitFor();await wait(200);
 const mobile=await page.locator(".composerShell").boundingBox();assert(mobile.x>=0&&mobile.x+mobile.width<=390&&mobile.y+mobile.height<=844,"mobile composer must fit unobstructed viewport");assert.equal(await page.locator(".workspaceSidebar").isVisible(),false);assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));await page.getByRole("button",{name:"Открыть меню чатов"}).click();assert.equal(await page.locator(".workspaceSidebar").isVisible(),true);await page.getByRole("button",{name:"Закрыть меню",exact:true}).click({position:{x:380,y:420}});assert.equal(await page.locator(".workspaceSidebar").isVisible(),false);
 await editor().fill("Мобильный черновик");const sentBeforeEnter=sends;await editor().press("Enter");assert.equal(sends,sentBeforeEnter);assert.equal(await editor().inputValue(),"Мобильный черновик\n");await editor().fill("Мобильный черновик");await context.setOffline(true);await wait(100);assert.equal(await page.getByRole("button",{name:"Отправить",exact:true}).isEnabled(),false);assert.equal(await editor().inputValue(),"Мобильный черновик");await context.setOffline(false);
 console.log("PASS mobile menu, layout and offline draft preservation");
 if(process.env.CHAT_UX_SCREENSHOTS){await mkdir(process.env.CHAT_UX_SCREENSHOTS,{recursive:true});await page.screenshot({path:`${process.env.CHAT_UX_SCREENSHOTS}/chat-mobile.png`});await page.setViewportSize({width:1440,height:960});await page.reload();await editor().waitFor();await wait(200);await page.screenshot({path:`${process.env.CHAT_UX_SCREENSHOTS}/chat-desktop.png`});}
 assert.equal(errors.filter(message=>!message.includes("eval()")).length,0,JSON.stringify(errors));
 console.log("CHAT UX BROWSER CHECK: PASS");
}finally{await browser.close();}
