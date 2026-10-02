import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {chromium} from 'playwright';
const base=process.env.CHAT_UX_BASE_URL??'http://127.0.0.1:3000';
const browser=await chromium.launch({executablePath:process.env.CHAT_UX_CHROMIUM,args:process.env.CHAT_UX_CHROMIUM?['--disable-gpu','--disable-software-rasterizer','--disable-features=Vulkan','--single-process']:[]});
const page=await browser.newPage({viewport:{width:1440,height:1000}});page.setDefaultTimeout(10000);const errors=[];page.on('pageerror',error=>errors.push(error.message));
const cid='00000000-0000-0000-0000-000000000001';let planCount=0,failSave=false,lastSave=null;
let agent={id:'agent-1',name:'Обзор рынка',role:'Исследователь',objective:'Исследуй рынок',instructions:'',autonomy:'semi_autonomous',status:'active',system_level:'balanced',tool_policy:{web:true},max_cost_rub_per_run:'10',max_cost_rub_per_day:'100',max_cost_rub_per_month:'1500',graph:{version:1,nodes:[{id:'start',title:'Начало',type:'notify'},{id:'ai',title:'AI обработка',type:'llm',prompt:'Подготовь обзор'},{id:'finish',title:'Готово',type:'finish'}],edges:[{from:'start',to:'ai'},{from:'ai',to:'finish'}]}};
await page.route('**/api/v1/**',async route=>{const req=route.request(),path=new URL(req.url()).pathname;let data={};
 if(path.includes('/auth/me/'))data={id:'user',username:'test',role:'customer',status:'active',email:'test@example.test'};
 else if(path.includes('/csrf/'))data={csrf_token:'test'};
 else if(path.endsWith('/agents/agent-1/'))data=agent;
 else if(path.includes('/agent-runs/'))data=[];
 else if(path.includes('/models/'))data=[{slug:'model-test',display_name:'Модель тест',available:true},{slug:'offline-model',display_name:'Недоступная модель',available:false}];
 else if(path.includes('/agent-connections/'))data=[{connection:cid,connection_name:'Мой сервис',connection_kind:'http',purpose:'http',enabled:true}];
 else if(path.includes('/readiness/'))data={ready:true,blockers:[],warnings:[],checks:{},actions:[]};
 else if(path.includes('/usage/'))data={can_start:true,run:{spent:'0',limit:'10'},day:{spent:'0',limit:'100'},month:{spent:'0',limit:'1500'},effective_remaining:'10',concurrency:{active:0,limit:2}};
 else if(path.includes('/wallet/'))data={available_rub:'100',reserved_rub:'0'};
 else if(path.includes('/ai-planner/preview/')){planCount++;await new Promise(resolve=>setTimeout(resolve,120));data={cost_rub:'0.12',draft:{graph:{version:1,nodes:[{id:'search',title:'Найти новости',type:'search',prompt:'Новости рынка'},{id:'summary',title:'Сделать обзор',type:'llm'}],edges:[{from:'search',to:'summary'}]}}};}
 else if(path.includes('/config/')){lastSave=req.postDataJSON();if(failSave){await route.fulfill({status:400,contentType:'application/json',body:JSON.stringify({detail:'Сохранение отклонено тестовым сервером'})});return;}agent={...agent,...lastSave};data=agent;}
 await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
});
const tile=id=>page.locator(`.react-flow__node[data-id="${id}"]`);
async function transitions(){const summary=page.getByText('Настроить переходы',{exact:true});if(!(await summary.evaluate(node=>node.parentElement.open)))await summary.click();}
try{
 await page.goto(`${base}/app/agents/agent-1`);await page.getByRole('button',{name:'Визуальная схема',exact:true}).click();await tile('ai').waitFor();assert.equal(await page.locator('.react-flow__node').count(),3);
 await tile('ai').click();await page.getByLabel('Модель LLM').selectOption('model-test');assert.equal(await page.getByRole('option',{name:'Недоступная модель · недоступна'}).evaluate(option=>option.disabled),true);
 await page.getByRole('button',{name:'+ Браузер: прочитать сайт',exact:true}).click();await page.getByLabel('URL страницы').fill('https://example.test/news');await page.getByLabel('Название',{exact:true}).fill('Прочитать новости');const browserId=await page.locator('.react-flow__node.selected').getAttribute('data-id');
 await transitions();await page.getByLabel('Следующий узел',{exact:true}).selectOption('finish');await tile('ai').click();await page.getByLabel('Следующий узел',{exact:true}).selectOption(browserId);
 await page.getByRole('button',{name:'Сохранить схему',exact:true}).click();await page.getByText('Изменения сохранены.',{exact:true}).waitFor();assert.equal(lastSave.graph.routing,'explicit');assert.equal(lastSave.graph.nodes.find(node=>node.id==='ai').selected_model,'model-test');assert(lastSave.graph.edges.some(edge=>edge.from==='ai'&&edge.to===browserId));assert(lastSave.graph.edges.some(edge=>edge.from===browserId&&edge.to==='finish'));assert.equal(planCount,0);
 console.log('PASS manual canvas nodes, selected LLM, explicit connections and persistence');
 const edgeCount=await page.locator('.react-flow__edge').count();await page.locator('.react-flow__edge').first().focus();await page.keyboard.press('Enter');await page.waitForFunction(()=>document.querySelectorAll('.react-flow__edge.selected').length===1);await page.keyboard.press('Delete');await page.waitForFunction(count=>document.querySelectorAll('.react-flow__edge').length===count-1,edgeCount);await page.getByRole('button',{name:'Сохранить схему',exact:true}).click();await page.getByText('Схема сохранена · Первый узел — начало',{exact:true}).waitFor();assert.equal(lastSave.graph.edges.length,edgeCount-1);
 console.log('PASS selected canvas edge deletion persists without rebuilding a chain');
 await page.getByRole('button',{name:'+ HTTP API / интеграция',exact:true}).click();await page.getByLabel('Подключение',{exact:true}).selectOption(cid);await page.getByLabel('Метод',{exact:true}).selectOption('POST');await page.getByLabel('Относительный путь').fill('v1/notes');await page.getByLabel('Тело JSON').fill('{bad');assert.equal(await page.getByRole('button',{name:'Сохранить схему',exact:true}).isEnabled(),false);await page.getByLabel('Тело JSON').fill('{"text":"{{previous_text}}"}');
 failSave=true;await page.getByRole('button',{name:'Сохранить схему',exact:true}).click();await page.getByText('Сохранение отклонено тестовым сервером',{exact:true}).first().waitFor();assert.equal(await page.getByRole('button',{name:'Сохранить схему',exact:true}).isEnabled(),true);failSave=false;await page.getByRole('button',{name:'Работа',exact:true}).click();await page.getByRole('button',{name:'Визуальная схема',exact:true}).click();assert.equal(await page.locator('.react-flow__node').count(),5);
 console.log('PASS HTTP bindings, POST template validation and failed-save recovery');
 await page.getByText('Собрать схему по описанию задачи',{exact:true}).click();await page.getByRole('textbox',{name:'Задача для конструктора'}).fill('Найди свежие новости рынка и подготовь краткий обзор');await page.getByRole('button',{name:'Предложить схему · платно',exact:true}).click();await page.getByRole('button',{name:'Применить черновик',exact:true}).waitFor();assert.equal(planCount,1);assert.equal(await page.locator('.react-flow__node').count(),5);await page.getByRole('button',{name:'Применить черновик',exact:true}).click();assert.equal(await page.locator('.react-flow__node').count(),2);await page.getByRole('button',{name:'Сохранить схему',exact:true}).click();await tile('search').waitFor();assert.equal(lastSave.tool_policy.web,true);
 console.log('PASS text-to-workflow proposal requires explicit apply and save');
 await page.setViewportSize({width:390,height:844});await page.reload();await page.getByRole('button',{name:'Визуальная схема',exact:true}).click();await tile('search').waitFor();assert(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),'mobile page must not overflow horizontally');assert(await page.getByLabel('Название',{exact:true}).isVisible());
 if(process.env.CHAT_UX_SCREENSHOTS){await mkdir(process.env.CHAT_UX_SCREENSHOTS,{recursive:true});await page.screenshot({path:`${process.env.CHAT_UX_SCREENSHOTS}/agent-mobile.png`,fullPage:true});await page.setViewportSize({width:1440,height:1000});await page.reload();await page.getByRole('button',{name:'Визуальная схема',exact:true}).click();await tile('search').waitFor();await page.waitForTimeout(300);await page.screenshot({path:`${process.env.CHAT_UX_SCREENSHOTS}/agent-desktop.png`,fullPage:true});}
 assert.equal(errors.length,0,JSON.stringify(errors));console.log('AGENT WORKFLOW BROWSER: PASS');
}finally{await browser.close()}
