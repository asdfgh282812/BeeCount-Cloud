import React from 'react'
import { createRoot } from 'react-dom/client'
import { LocaleProvider } from '@beecount/ui'
import { AuthProvider } from './context/AuthContext'
import { dictionaries } from './i18n'
import { FarmLeaderboardPage } from './pages/sections/FarmLeaderboardPage'
import './styles.css'
window.fetch = async input => new Response(JSON.stringify({board:new URL(String(input),location.origin).searchParams.get('board'),week:'2026-10-05',availableWeeks:['2026-10-05'],playerCount:7,averageScore:210,myValue:199,myBookkeepingScore:199,items:[{rank:6,userId:'other',displayName:'隔壁農場',avatarUrl:null,value:205,rewardCoins:100,title:null,isMe:false},{rank:7,userId:'preview',displayName:'我的場',avatarUrl:null,value:199,rewardCoins:100,title:null,isMe:true}]}),{headers:{'Content-Type':'application/json'}})
createRoot(document.getElementById('root')!).render(<LocaleProvider dictionaries={dictionaries}><AuthProvider token="preview" profileMe={null} sessionUserId={null} isAdmin isAdminResolved refreshProfile={async()=>{}} logout={()=>{}}><main className="mx-auto max-w-4xl p-6"><FarmLeaderboardPage /></main></AuthProvider></LocaleProvider>)
