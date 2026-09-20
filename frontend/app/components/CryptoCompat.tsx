"use client";

import {useEffect} from "react";

function fallbackUuid(){
  try{
    const cryptoApi=globalThis.crypto;
    if(cryptoApi&&typeof cryptoApi.getRandomValues==="function"){
      const bytes=new Uint8Array(16);
      cryptoApi.getRandomValues(bytes);
      bytes[6]=(bytes[6]&15)|64;
      bytes[8]=(bytes[8]&63)|128;
      const hex=Array.from(bytes,b=>b.toString(16).padStart(2,"0")).join("");
      return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
    }
  }catch{}
  const seed=`${Date.now().toString(16)}${Math.random().toString(16).slice(2)}${Math.random().toString(16).slice(2)}`.padEnd(32,"0").slice(0,32);
  return `${seed.slice(0,8)}-${seed.slice(8,12)}-4${seed.slice(13,16)}-8${seed.slice(17,20)}-${seed.slice(20,32)}`;
}

export default function CryptoCompat(){
  useEffect(()=>{
    try{
      const cryptoApi=globalThis.crypto as Crypto & {randomUUID?:()=>string};
      if(cryptoApi&&typeof cryptoApi.randomUUID!=="function"){
        Object.defineProperty(cryptoApi,"randomUUID",{value:fallbackUuid,configurable:true});
      }
    }catch{}
  },[]);
  return null;
}
