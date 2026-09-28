"use client";

import {useEffect} from "react";

export default function ClientCabinetError({error,reset}:{error:Error&{digest?:string};reset:()=>void}){
 useEffect(()=>{console.error("Client cabinet route error",{name:error.name,message:error.message,digest:error.digest})},[error]);
 return <main className="clientRouteError" role="alert"><section><span>AI WORKSPACE</span><h1>Этот экран не удалось открыть</h1><p>Ваши данные не потеряны. Попробуйте восстановить экран или вернитесь в чат. Если ошибка повторяется, поддержке достаточно времени ошибки и названия раздела — пароли и API-ключи отправлять не нужно.</p><div><button type="button" onClick={reset}>Повторить</button><a href="/app">Вернуться в чат</a></div>{error.digest&&<small>Код диагностики: {error.digest}</small>}</section></main>;
}
