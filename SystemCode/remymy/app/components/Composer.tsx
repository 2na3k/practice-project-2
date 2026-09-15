"use client";

import { ArrowUp, Square, Sparkles, ChevronDown } from "lucide-react";
import { useState } from "react";
type ComposerProps = {onSubmit:(text:string)=>void; onStop:()=>void; busy:boolean; welcome:boolean; modelLabel:string; onConnection:()=>void};
const chips=["Ready in 20","Plant-powered","Protein please","Surprise me"];
export function Composer({onSubmit,onStop,busy,welcome,modelLabel,onConnection}:ComposerProps){
 const [value,setValue]=useState("");
 const submit=()=>{const text=value.trim();if(!text||busy)return;onSubmit(text);setValue("");};
 return <div className={`composer-wrap ${welcome?"welcome-composer":""}`}>
  <section className="composer-card" aria-label="Meal suggestion composer">
   {welcome&&<div className="composer-label"><Sparkles size={15}/>Tell me what sounds good</div>}
   <div className="composer-row"><textarea className="composer-input" value={value} onChange={event=>setValue(event.target.value)} onKeyDown={event=>{if(event.key==="Enter"&&!event.shiftKey&&!event.nativeEvent.isComposing){event.preventDefault();submit();}}} placeholder={welcome?"Something quick, filling, and a little spicy…":"What's on your mind?"} maxLength={2000} rows={2} aria-label="Message Remymy"/><button className="composer-submit" onClick={busy?onStop:submit} disabled={!busy&&!value.trim()} aria-label={busy?"Stop response":"Send message"}>{busy?<Square size={15} fill="currentColor"/>:<ArrowUp size={20}/>}</button></div>
   <div className="composer-bottom"><button className="composer-model" onClick={onConnection}><span className="connection-dot"/>{modelLabel}<ChevronDown size={12}/></button><span className="composer-hint">Enter to send · Shift + Enter for a new line</span></div>
  </section>
  {welcome&&<div className="welcome-prompts">{chips.map((chip,index)=><button key={chip} disabled={busy} onClick={()=>onSubmit(chip)}><span>{["◷","❋","↗","✧"][index]}</span>{chip}</button>)}</div>}
 </div>;
}
