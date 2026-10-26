"use client";

import { MessageCircle, Heart, History, SlidersHorizontal, UtensilsCrossed, LayoutGrid, NotebookPen, BookOpen, Plug, Plus } from "lucide-react";
import type { ConnectionStatus } from "@/lib/chat-types";
import { UserAvatar } from "@/components/UserAvatar";
import { DEMO_PROFILE } from "@/lib/demo-profile";
export type AppView = "discover" | "meals" | "add-recipe" | "my-recipes" | "saved" | "history" | "preferences" | "connection";
type SidebarProps = { view: AppView; onViewChange: (view: AppView) => void; onNewChat: () => void; connection: ConnectionStatus };
const items = [
  {id:"discover" as const,label:"Discover",icon:MessageCircle},
  {id:"meals" as const,label:"Meals",icon:LayoutGrid},
  {id:"add-recipe" as const,label:"Add recipe",icon:NotebookPen},
  {id:"my-recipes" as const,label:"My recipes",icon:BookOpen},
  {id:"saved" as const,label:"Saved",icon:Heart},
  {id:"history" as const,label:"History",icon:History},
  {id:"preferences" as const,label:"Preferences",icon:SlidersHorizontal},
  {id:"connection" as const,label:"AI connection",icon:Plug},
];
export function Sidebar({view,onViewChange,onNewChat,connection}:SidebarProps){
 return <>
  <aside className="app-sidebar">
    <button className="brand-mark" onClick={()=>onViewChange("discover")} aria-label="Go to Remymy discover"><span className="brand-icon"><UtensilsCrossed size={17}/></span><span>Remymy</span></button>
    <button className="new-chat-button" onClick={onNewChat}><Plus size={16}/>New chat</button>
    <div className="sidebar-section-label">YOUR LITTLE FOOD CORNER</div>
    <nav className="sidebar-nav" aria-label="Main navigation">{items.map(({id,label,icon:Icon})=><button key={id} className={`nav-item ${view===id?"is-active":""}`} onClick={()=>onViewChange(id)} aria-current={view===id?"page":undefined}><Icon size={18} strokeWidth={1.8}/><span>{label}</span></button>)}</nav>
    <button className={`sidebar-connection ${connection.connected?"connected":""}`} onClick={()=>onViewChange("connection")}><span className="connection-dot"/><span><strong>{connection.connected?"AI is connected":"Make it a conversation"}</strong><small>{connection.connected?connection.provider:"Connect your own AI model"}</small></span><Plug size={15}/></button>
    <button className={`sidebar-profile ${view==="preferences"?"is-active":""}`} onClick={()=>onViewChange("preferences")} aria-label={`${DEMO_PROFILE.name}, open your preferences`}><UserAvatar size={34}/><span><strong>{DEMO_PROFILE.name}</strong><small>@{DEMO_PROFILE.username} · {DEMO_PROFILE.tagline}</small></span></button>
  </aside>
  <nav className="mobile-nav" aria-label="Mobile navigation">{items.map(({id,label,icon:Icon})=><button key={id} className={`nav-item ${view===id?"is-active":""}`} onClick={()=>onViewChange(id)} aria-current={view===id?"page":undefined}><Icon size={18}/><span>{id==="connection"?"Connect":id==="add-recipe"?"Add":id==="my-recipes"?"Mine":label}</span></button>)}</nav>
 </>;
}
