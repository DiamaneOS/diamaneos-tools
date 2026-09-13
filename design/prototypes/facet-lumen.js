/* Interactive design fixtures. No network, permissions, or device state is changed. */
(()=>{
  'use strict';
  const concepts=[{id:'lumen',name:'Lumen'}];
  const config={theme:'light',scale:1,locale:'en',scenario:'normal',reduced:false};
  const apps=[['Files','folder','files'],['Settings','settings','settings'],['Meadow','messages-square','meadow'],['Atlas','compass','atlas']];
  const settings=[['Network & internet','Wi-Fi, mobile network','wifi','network'],['Connected devices','Bluetooth','bluetooth','devices'],['Apps & privacy','Permissions, app access','shield','privacy'],['Notifications','App notifications, quiet hours','bell','notifications'],['Battery','76% · Charging','battery-medium','battery'],['Display & appearance','Theme, text size','sun','display'],['System update','Preview 02 available','download','updates']];
  const de={Home:'Start',Settings:'Einstellungen',Files:'Dateien',Search:'Suchen','All apps':'Alle Apps','Search apps and settings':'Apps und Einstellungen suchen','Search settings':'Einstellungen suchen','Personal profile':'Persönliches Profil','On this device':'Auf diesem Gerät','Thursday, 10 September':'Donnerstag, 10. September','Thursday':'Donnerstag','10 September':'10. September','Open notifications':'Mitteilungen öffnen','Close notifications':'Mitteilungen schließen',Back:'Zurück',Recents:'Letzte Apps','Wi-Fi':'WLAN',Bluetooth:'Bluetooth','Do not disturb':'Nicht stören',Flashlight:'Taschenlampe',On:'An',Off:'Aus',Connected:'Verbunden',Brightness:'Helligkeit',Notifications:'Mitteilungen','Network & internet':'Netzwerk & Internet','Wi-Fi, mobile network':'WLAN, Mobilfunknetz','Connected devices':'Verbundene Geräte','Apps & privacy':'Apps & Datenschutz','Permissions, app access':'Berechtigungen, App-Zugriff','App notifications, quiet hours':'App-Mitteilungen, Ruhezeiten',Battery:'Akku','76% · Charging':'76% · Wird geladen','Display & appearance':'Anzeige & Darstellung','Theme, text size':'Design, Schriftgröße','System update':'Systemupdate','Preview 02 available':'Vorschau 02 verfügbar','Preview 01 installed':'Vorschau 01 installiert','Download complete':'Download abgeschlossen','Weekend itinerary.pdf':'Wochenendplanung.pdf','Coffee after the walk?':'Kaffee nach dem Spaziergang?','Maya':'Maya',now:'jetzt','2 min':'vor 2 Min.',Open:'Öffnen',Dismiss:'Entfernen','Notification dismissed':'Mitteilung entfernt',Undo:'Rückgängig',Close:'Schließen','You’re all caught up':'Alles erledigt','New notifications will appear here.':'Neue Mitteilungen erscheinen hier.','Couldn’t read connection status':'Verbindungsstatus nicht verfügbar','Your settings have not changed. Try again.':'Deine Einstellungen wurden nicht geändert. Versuche es erneut.',Retry:'Erneut versuchen','No results':'Keine Ergebnisse','Try another name.':'Versuche einen anderen Namen.','All settings':'Alle Einstellungen','No recent apps':'Keine letzten Apps','Open an app from Home to see it here.':'Öffne eine App auf dem Startbildschirm.','Downloads':'Downloads','Today · 184 KB':'Heute · 184 KB','Yesterday · 2 KB':'Gestern · 2 KB','No downloads yet':'Noch keine Downloads','Downloaded files will appear here.':'Heruntergeladene Dateien erscheinen hier.','Return to Home':'Zum Startbildschirm','App notifications':'App-Mitteilungen','Meadow notifications':'Meadow-Mitteilungen','Allowed':'Erlaubt','Blocked':'Blockiert','Network access':'Netzwerkzugriff','Sensor access':'Sensorzugriff','Charging':'Wird geladen','Charge limit':'Ladegrenze','Requested':'Angefordert','Last confirmed':'Zuletzt bestätigt','Apply limit':'Ladegrenze übernehmen','Limit acknowledged':'Ladegrenze bestätigt','Light theme':'Helles Design','Dark theme':'Dunkles Design','Follow system':'Systemeinstellung','Current theme':'Aktuelles Design','Download update':'Update herunterladen','Update details':'Update-Details','Ready to download':'Zum Herunterladen bereit','Download queued in this concept':'Download in diesem Entwurf vorgemerkt','No package has been downloaded or installed.':'Kein Paket wurde heruntergeladen oder installiert.','Messages':'Nachrichten','Approximate location':'Ungefährer Standort','Not requested':'Nicht angefragt','Find nearby trails':'Wanderwege in der Nähe','Location request preview':'Vorschau der Standortanfrage','Cancel':'Abbrechen','No permission has been granted.':'Es wurde keine Berechtigung erteilt.','Morning walk':'Morgenspaziergang','Meet at the station at 10:00.':'Treffen um 10:00 Uhr am Bahnhof.','Bring a water bottle.':'Eine Wasserflasche mitbringen.','Sounds good. See you there!':'Klingt gut. Bis dann!','Reply preview':'Antwortvorschau','Nothing was sent.':'Es wurde nichts gesendet.','Clear search':'Suche löschen','Updated':'Aktualisiert'};
  const t=x=>config.locale==='de'?(de[x]||x):x;
  const esc=x=>String(x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const icon=n=>`<i data-lucide="${n}" aria-hidden="true"></i>`;
  const button=(label,action,cls='action')=>`<button class="${cls}" data-action="${action}">${esc(t(label))}</button>`;
  const ib=(label,action,name)=>`<button data-action="${action}" aria-label="${esc(t(label))}">${icon(name)}</button>`;
  const states=concepts.map(c=>fresh(c));
  function baseFresh(c){return {...c,page:'home',stack:[],wifi:true,bluetooth:false,dnd:false,torch:false,bright:65,notifications:true,network:true,sensors:false,wanted:80,confirmed:80,query:'',expanded:'',dismissed:[],lastDismissed:'',toast:'',resolved:false,queued:false,reply:false,location:false,recents:[],animation:null};}
  function theme(s){const mode=s.theme||config.theme;return mode==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):mode;}
  function phone(s){return document.getElementById('phone-'+s.id);}
  function paintIcons(root){if(window.lucide)window.lucide.createIcons({root,attrs:{'stroke-width':1.7}});}
  function baseTitle(s){return {home:'Home',shade:'Notifications',settings:'Settings',apps:'All apps',search:'Search',recents:'Recents',files:'Downloads',file:'Weekend itinerary.pdf',meadow:'Messages',atlas:'Atlas',network:'Network & internet',devices:'Connected devices',privacy:'Apps & privacy',notifications:'Notifications',battery:'Battery',display:'Display & appearance',updates:'System update'}[s.page]||'Settings';}
  function bar(s,name){return `<div class="toolbar">${ib('Back','back','arrow-left')}<h3>${esc(t(name))}</h3></div>`;}
  function row(name,detail,ic,action){return `<button class="setting-row" data-action="${action}"><span class="row-icon">${icon(ic)}</span><span class="row-copy"><strong>${esc(t(name))}</strong>${detail?`<small>${esc(t(detail))}</small>`:''}</span><i data-lucide="chevron-right" class="row-chevron" aria-hidden="true"></i></button>`;}
  function toggle(s,label,key,detail){return `<button class="setting-row" role="switch" aria-checked="${s[key]}" data-action="toggle:${key}"><span class="row-copy"><strong>${esc(t(label))}</strong><small>${esc(t(detail||(s[key]?'On':'Off')))}</small></span><span class="switch" aria-hidden="true"></span></button>`;}
  function searchField(label,value=''){return `<label class="search-field">${icon('search')}<input type="search" aria-label="${esc(t(label))}" placeholder="${esc(t(label))}" value="${esc(value)}" autocomplete="off" data-query></label>`;}
  function empty(heading,copy,ic='search'){return `<div class="empty">${icon(ic)}<h3>${esc(t(heading))}</h3><p>${esc(t(copy))}</p></div>`;}
  function connectionError(s){return config.scenario==='failure'&&!s.resolved?`<div class="notice error" role="alert"><strong>${esc(t('Couldn’t read connection status'))}</strong><p>${esc(t('Your settings have not changed. Try again.'))}</p>${button('Retry','retry','action secondary')}</div>`:'';}
  function appGrid(){return `<div class="app-grid">${apps.map(([name,ic,page])=>`<button class="app" data-action="go:${page}"><span class="app-icon ${page}">${icon(ic)}</span><span class="app-name">${esc(t(name))}</span></button>`).join('')}</div>`;}
  function home(s){const clock=s.id==='contour'?'<span>09</span><span>41</span>':'09:41';return `<div class="home-screen"><div class="home-upper" data-swipe="home"><p class="home-date">${esc(t('Thursday, 10 September'))}</p><div class="home-clock" role="img" aria-label="09:41">${clock}</div><div class="home-profile">${icon('user-round')}<span>${esc(t('Personal profile'))}</span></div></div><div class="home-space"></div>${appGrid()}<button class="home-search" data-action="go:search">${icon('search')}<span>${esc(t('Search apps and settings'))}</span></button><button class="all-apps" data-action="go:apps" data-swipe="apps">${icon('chevron-up')}<span>${esc(t('All apps'))}</span></button></div>`;}
  function settingsResults(s){const q=s.query.trim().toLocaleLowerCase();const list=settings.filter(([name,detail])=>(t(name)+' '+t(detail)).toLocaleLowerCase().includes(q));if(!list.length)return empty('No results','Try another name.');return `<div class="settings-group">${list.map(([name,detail,ic,page],i)=>row(name,page==='updates'?(s.updateStage==='complete'?'Preview 02 installed':['downloading','verifying','installing'].includes(s.updateStage)?'Update in progress':s.updateStage==='ready'?'Ready to restart':detail):detail,ic,'go:'+page)+(i===3&&!q?'</div><div class="settings-group">':'')).join('')}</div>`;}
  function settingsPage(s){return `<h3 class="page-title">${esc(t('Settings'))}</h3>${searchField('Search settings',s.query)}<div class="profile-row"><span class="avatar">${icon('user-round')}</span><span><strong>${esc(t('Personal profile'))}</strong><small>${esc(t('On this device'))}</small></span></div><div class="results">${settingsResults(s)}</div><p class="subtle">DiamaneOS · ${esc(t(s.updateStage==='complete'?'Preview 02 installed':'Preview 01 installed'))}</p>`;}
  function quick(s,label,ic,key){const value=(key==='wifi'&&s.wifi)?'Home':s[key]?'On':'Off';return `<button class="quick" aria-pressed="${s[key]}" data-action="toggle:${key}">${icon(ic)}<span><strong>${esc(t(label))}</strong><small>${key==='wifi'&&s.wifi?'Home':esc(t(value))}</small></span></button>`;}
  function notification(s,key){const isMessage=key==='meadow';return `<div class="notification"><div class="notification-top">${icon(isMessage?'messages-square':'folder')}<span>${isMessage?'Meadow':esc(t('Files'))}</span><time>${esc(t(isMessage?'2 min':'now'))}</time>${ib('Dismiss','dismiss:'+key,'x')}</div><button class="notification-open" data-action="expand:${key}" aria-expanded="${s.expanded===key}"><strong>${isMessage?'Maya':esc(t('Download complete'))}</strong><span>${esc(t(isMessage?'Coffee after the walk?':'Weekend itinerary.pdf'))}</span></button>${s.expanded===key?`<div class="notification-detail">${button(isMessage?'Open':'Open','go:'+(isMessage?'meadow':'file'),'action secondary')}</div>`:''}</div>`;}
  function baseShade(s){const notes=config.scenario==='empty'?[]:['meadow','files'].filter(key=>!s.dismissed.includes(key));return `<div class="shade-header" data-swipe="shade"><div style="flex:1"><h3>${esc(t('Thursday'))}</h3><p>${esc(t('10 September'))}</p></div>${ib('Settings','go:settings','settings')}</div>${connectionError(s)}<div class="quick-grid">${quick(s,'Wi-Fi','wifi','wifi')}${quick(s,'Bluetooth','bluetooth','bluetooth')}${quick(s,'Do not disturb','moon','dnd')}${quick(s,'Flashlight','flashlight','torch')}</div><label class="brightness">${icon('sun')}<input type="range" min="0" max="100" value="${s.bright}" data-bright aria-label="${esc(t('Brightness'))}"><output>${s.bright}%</output></label><p class="section-label">${esc(t('Notifications'))}</p>${notes.length?notes.map(k=>notification(s,k)).join(''):empty('You’re all caught up','New notifications will appear here.','check')}<button class="close-shade" data-action="back" data-swipe="shade">${icon('chevron-up')}<span>${esc(t('Close notifications'))}</span></button>`;}
  function searchResults(s){const q=s.query.trim().toLocaleLowerCase();const list=[...apps.map(([n,i,p])=>[n,'',i,p]),...settings.filter(x=>!['privacy','notifications','display','devices'].includes(x[3]))].filter(([n,d])=>(t(n)+' '+t(d)).toLocaleLowerCase().includes(q));return list.length?`<div class="settings-group">${list.map(([n,d,i,p])=>row(n,d,i,'go:'+p)).join('')}</div>`:empty('No results','Try another name.');}
  function baseSupport(s){
    const heading=`${bar(s,'Settings')}<h3 class="page-title">${esc(t(title(s)))}</h3>`;
    if(s.page==='apps')return `${bar(s,'Home')}<h3 class="page-title">${esc(t('All apps'))}</h3>${button('Search','go:search','home-search')}${appGrid()}`;
    if(s.page==='search')return `${bar(s,'Home')}${searchField('Search apps and settings',s.query)}<div class="results">${searchResults(s)}</div>`;
    if(s.page==='recents')return `${bar(s,'Home')}<h3 class="page-title">${esc(t('Recents'))}</h3>${s.recents.length?`<div class="settings-group">${s.recents.map(p=>{const a=apps.find(a=>a[2]===p);return row(a[0],'',a[1],'go:'+p)}).join('')}</div>`:empty('No recent apps','Open an app from Home to see it here.','panels-top-left')}`;
    if(s.page==='network')return heading+connectionError(s)+`<div class="settings-group">${toggle(s,'Wi-Fi','wifi',s.wifi?'Home':'Off')}</div><p class="subtle">${esc(t('Personal profile'))}</p>`;
    if(s.page==='devices')return heading+`<div class="settings-group">${toggle(s,'Bluetooth','bluetooth')}</div>`;
    if(s.page==='notifications')return heading+`<div class="settings-group">${toggle(s,'Meadow notifications','notifications')}</div>`;
    if(s.page==='privacy')return heading+`<div class="profile-row"><span class="app-icon meadow">${icon('messages-square')}</span><span><strong>Meadow</strong><small>${esc(t('Personal profile'))}</small></span></div><div class="settings-group">${toggle(s,'Network access','network',s.network?'Allowed':'Blocked')}${toggle(s,'Sensor access','sensors',s.sensors?'Allowed':'Blocked')}</div><p class="subtle">${esc(t('Permissions, app access'))}</p>`;
    if(s.page==='files')return `${bar(s,'Home')}<h3 class="page-title">${esc(t('Downloads'))}</h3><p class="subtle">${esc(t('On this device'))}</p>${config.scenario==='empty'?empty('No downloads yet','Downloaded files will appear here.','folder'): `<div class="settings-group">${row('Weekend itinerary.pdf','Today · 184 KB','file-text','go:file')}${row('Notes.txt','Yesterday · 2 KB','file-text','go:notes')}</div>`}`;
    if(s.page==='file'||s.page==='notes')return `${bar(s,'Files')}<h3 class="page-title">${esc(t(s.page==='file'?'Weekend itinerary.pdf':'Notes.txt'))}</h3><div class="document-sample"><h4>${esc(t('Morning walk'))}</h4><p>${esc(t('Meet at the station at 10:00.'))}</p><p>${esc(t('Bring a water bottle.'))}</p></div>`;
    if(s.page==='meadow')return `${bar(s,'Home')}<h3 class="page-title">Meadow</h3><p class="subtle">Maya · ${esc(t('Personal profile'))}</p><div class="notice">${esc(t('Coffee after the walk?'))}</div>${s.reply?`<div class="notice"><strong>${esc(t('Reply preview'))}</strong>${esc(t('Nothing was sent.'))}</div>`:button('Sounds good. See you there!','reply')}`;
    if(s.page==='atlas')return `${bar(s,'Home')}<h3 class="page-title">Atlas</h3><p class="details-copy">${esc(t('Find nearby trails'))}</p><div class="settings-group">${row('Approximate location',s.location?'Not requested':'Not requested','map-pin','location')}</div>${s.location?`<div class="notice"><strong>${esc(t('Location request preview'))}</strong><p>${esc(t('No permission has been granted.'))}</p>${button('Cancel','cancel-location','action secondary')}</div>`:button('Find nearby trails','location')}`;
    if(s.page==='battery')return heading+`<p class="details-copy"><strong>76%</strong> · ${esc(t('Charging'))}</p><div class="notice"><strong>${esc(t('Charge limit'))}</strong><label>${esc(t('Requested'))}: <output data-limit-value>${s.wanted}%</output><input type="range" style="display:block;width:100%;height:48px" aria-label="${esc(t('Charge limit'))}" data-limit min="70" max="100" step="5" value="${s.wanted}"></label><p>${esc(t('Last confirmed'))}: ${s.confirmed}%</p>${button('Apply limit','apply-limit')}</div>`;
    if(s.page==='display')return heading+`<p class="subtle">${esc(t('Current theme'))}: ${esc(t(theme(s)==='dark'?'Dark theme':'Light theme'))}</p><div class="settings-group">${row('Light theme','','sun','theme:light')}${row('Dark theme','','moon','theme:dark')}${row('Follow system','','monitor','theme:system')}</div>`;
    if(s.page==='updates')return heading+`<div class="notice"><strong>DiamaneOS preview 02</strong><p>428 MB · ${esc(t('Ready to download'))}</p><p>${esc(t('Preview 01 installed'))}</p></div>${s.queued?`<div class="notice"><strong>${esc(t('Download queued in this concept'))}</strong><p>${esc(t('No package has been downloaded or installed.'))}</p></div>`:button('Download update','queue-update')}`;
    return heading;
  }
  function render(s,{focus='',motion='',restoreScroll=null}={}){
    const p=phone(s),oldScreen=p.querySelector('.screen'),scroll=restoreScroll??oldScreen?.scrollTop??0;const outgoing=['shade-out','drawer-out','search-out'].includes(motion)&&oldScreen?oldScreen.cloneNode(true):null;s.animation?.cancel();p.dataset.theme=theme(s);p.dataset.large=String(config.scale>=1.5);p.dataset.reduced=String(config.reduced);p.dir=config.locale==='rtl'?'rtl':'ltr';p.lang=config.locale==='de'?'de':'en';p.style.setProperty('--scale',config.scale);
    const active=document.activeElement;
    const remembered=active?.closest('.phone')===p&&active.dataset.action?{action:active.dataset.action,scope:active.closest('.nav-bar')?'.nav-bar':'.screen'}:null;
    p.innerHTML=`<button class="statusbar" data-action="${s.page==='shade'?'back':'go:shade'}" data-swipe="status" aria-label="${esc(t(s.page==='shade'?'Close notifications':'Open notifications'))}" aria-expanded="${s.page==='shade'}"><span>09:41</span><span class="signal">${icon('signal')}${icon(s.wifi?'wifi':'wifi-off')}76% ${icon('battery-medium')}</span></button><div class="viewport"><section class="screen" tabindex="-1" aria-label="${esc(t(title(s)))}">${s.page==='home'?home(s):s.page==='shade'?shade(s):s.page==='settings'?settingsPage(s):support(s)}</section><div class="toast" role="status" aria-live="polite" ${s.toast?'':'hidden'}><span>${esc(t(s.toast))}</span>${s.lastDismissed?button('Undo','undo','toast-action'):ib('Close','clear-toast','x')}</div></div><nav class="nav-bar" aria-label="${s.name} ${esc(t('Home'))}">${ib('Back','back','chevron-left')}<button class="home-control" data-action="home" aria-label="${esc(t('Home'))}">${icon('circle')}</button>${ib('Recents','go:recents','square')}</nav>`;
    paintIcons(p);const screen=p.querySelector('.screen');screen.scrollTop=scroll;
    if(motion&&!config.reduced&&!matchMedia('(prefers-reduced-motion: reduce)').matches){if(outgoing){outgoing.classList.add('screen-ghost');outgoing.inert=true;outgoing.setAttribute('aria-hidden','true');outgoing.removeAttribute('aria-label');outgoing.querySelectorAll('[data-action],[id]').forEach(e=>{e.removeAttribute('data-action');e.removeAttribute('id')});p.querySelector('.viewport').append(outgoing);s.animation=outgoing.animate([{transform:'none'},{transform:motion==='drawer-out'?'translateY(100%)':'translateY(-100%)'}],{duration:220,easing:'cubic-bezier(.4,0,.2,1)'});s.animation.onfinish=()=>outgoing.remove();s.animation.oncancel=()=>outgoing.remove();}else{const rtl=config.locale==='rtl'?-1:1;const delta=motion==='drawer'?'translateY(100%)':['shade','search'].includes(motion)?'translateY(-100%)':`translateX(${(motion==='back'?-10:10)*rtl}px)`;s.animation=screen.animate([{transform:delta,opacity:['shade','drawer','search'].includes(motion)?1:.82},{transform:'none',opacity:1}],{duration:['shade','drawer','search'].includes(motion)?230:170,easing:'cubic-bezier(.16,1,.3,1)'});}}
    if(focus){const el=focus==='query'?p.querySelector('[data-query]'):Array.from(p.querySelectorAll('[data-action]')).find(e=>e.dataset.action===focus);(el||screen).focus({preventScroll:true});}
    if(!focus&&remembered){const target=p.querySelector(remembered.scope)?.querySelector('[data-action="'+CSS.escape(remembered.action)+'"]');(target||screen).focus({preventScroll:true});}
    renderModal(s);
  }
  function baseRoute(s,page){const p=phone(s),active=document.activeElement;s.stack.push({page:s.page,query:s.query,scroll:p.querySelector('.screen').scrollTop,focus:active?.dataset.action||''});s.page=page;s.query='';s.toast='';s.lastDismissed='';if(apps.some(a=>a[2]===page))s.recents=[page,...s.recents.filter(x=>x!==page)].slice(0,4);render(s,{focus:page==='search'?'query':'screen',motion:page==='shade'?'shade':page==='apps'?'drawer':page==='search'&&s.stack.at(-1)?.page==='home'?'search':'forward',restoreScroll:0});}
  function baseBack(s){const old=s.page,prev=s.stack.pop();s.page=prev?.page||'home';s.query=prev?.query||'';s.toast='';s.lastDismissed='';render(s,{focus:prev?.focus||'screen',motion:old==='shade'?'shade-out':old==='apps'?'drawer-out':old==='search'&&s.page==='home'?'search-out':'back',restoreScroll:prev?.scroll||0});}
  function baseAction(s,key){
    if(key.startsWith('go:'))return route(s,key.slice(3));
    if(key==='back')return back(s);
    if(key==='home'){const old=s.page;s.stack=[];s.page='home';s.query='';s.toast='';s.lastDismissed='';return render(s,{focus:'home',motion:old==='shade'?'shade-out':old==='apps'?'drawer-out':old==='search'?'search-out':'back',restoreScroll:0});}
    if(key.startsWith('toggle:')){const k=key.slice(7);s[k]=!s[k];render(s,{focus:key});return;}
    if(key.startsWith('expand:')){const k=key.slice(7);s.expanded=s.expanded===k?'':k;render(s,{focus:key});return;}
    if(key.startsWith('dismiss:')){const k=key.slice(8);s.dismissed.push(k);s.lastDismissed=k;s.toast='Notification dismissed';render(s,{focus:'undo'});return;}
    if(key==='undo'){s.dismissed=s.dismissed.filter(k=>k!==s.lastDismissed);s.lastDismissed='';s.toast='';render(s,{focus:'screen'});return;}
    if(key==='clear-toast'){s.toast='';render(s,{focus:'screen'});return;}
    if(key==='retry'){s.resolved=true;s.toast='Updated';render(s,{focus:'clear-toast'});return;}
    if(key==='apply-limit'){s.confirmed=s.wanted;s.toast='Limit acknowledged';render(s,{focus:key});return;}
    if(key==='reply'){s.reply=true;render(s,{focus:'screen'});return;}
    if(key==='location'||key==='cancel-location'){s.location=key==='location';render(s,{focus:s.location?'cancel-location':'location'});return;}
    if(key==='queue-update'){s.queued=true;render(s,{focus:'screen'});return;}
    if(key.startsWith('theme:')){const v=key.slice(6);s.theme=v;render(s,{focus:key});}
  }
  // Six-journey extension of the selected Lumen comparison. All state is fictional.
  const journeyGuides = {
    home: 'Find an app or setting using the visible search. Open a download, then return with Back.',
    setup: 'Review the public sample phrase, practise all six words, and recover from a wrong answer. Never enter a real secret.',
    privacy: 'Change Meadow’s preset, inspect the exact consequences, and apply. Try Failure to inspect an incomplete result.',
    notifications: 'Find why Meadow is quiet, change only the affected setting, then test delivery. Failure explores a stopped Work profile.',
    battery: 'Request a charge limit, compare it with the confirmed value, and inspect health or the export preview.',
    updates: 'Inspect package identity, follow download and verification, then confirm restart. Failure rejects a damaged download.'
  };
  Object.assign(de, {
    'Welcome to your phone':'Willkommen auf deinem Smartphone', 'A little setup. Then it’s yours.':'Ein paar Schritte. Dann gehört es dir.', 'Start setup':'Einrichtung starten', 'Language & accessibility':'Sprache & Bedienungshilfen', 'Your passphrase':'Deine Passphrase', 'Review your phrase':'Passphrase überprüfen', 'Practise your phrase':'Passphrase üben', 'Show sample words':'Beispielwörter anzeigen', 'Hide words':'Wörter ausblenden', 'Continue to practice':'Weiter zur Übung', 'Finish practice':'Übung abschließen', 'Review words':'Wörter erneut ansehen', 'Undo last word':'Letztes Wort entfernen', 'Try the phrase again':'Passphrase erneut versuchen', 'That order doesn’t match':'Die Reihenfolge stimmt nicht', 'Practice complete':'Übung abgeschlossen', 'Go to Home':'Zum Startbildschirm', 'Nothing has been set. Try again.':'Es wurde nichts eingerichtet. Versuche es erneut.', 'Couldn’t finish setup':'Einrichtung nicht abgeschlossen', 'Preparing your phrase':'Passphrase wird vorbereitet', 'Phrase unavailable':'Passphrase nicht verfügbar', 'No phrase to confirm':'Keine Passphrase zum Bestätigen', 'Try again':'Erneut versuchen', 'Change preset':'Voreinstellung ändern', 'Choose a preset':'Voreinstellung auswählen', 'Review changes':'Änderungen überprüfen', 'Apply changes':'Änderungen übernehmen', 'Current preset':'Aktuelle Voreinstellung', 'No change':'Keine Änderung', 'Selected files':'Ausgewählte Dateien', 'Selected contacts':'Ausgewählte Kontakte', 'Individual controls':'Einzelne Berechtigungen', 'Some changes weren’t applied':'Einige Änderungen wurden nicht übernommen', 'Retry failed change':'Fehlgeschlagene Änderung wiederholen', 'Changes applied':'Änderungen übernommen', 'Reading app permissions':'App-Berechtigungen werden gelesen', 'Permissions unavailable':'Berechtigungen nicht verfügbar', 'No app selected':'Keine App ausgewählt', 'Find out why':'Ursache finden', 'Missing notifications?':'Fehlen Mitteilungen?', 'Notifications are off':'Mitteilungen sind ausgeschaltet', 'Turn on notifications':'Mitteilungen einschalten', 'Permission is on':'Berechtigung ist aktiviert', 'Send sample test':'Beispieltest senden', 'Test notification received':'Testmitteilung empfangen', 'Work profile':'Arbeitsprofil', 'Work is stopped':'Arbeitsprofil ist beendet', 'Open profile settings':'Profileinstellungen öffnen', 'Return to Personal':'Zum persönlichen Profil', 'Unlock on your device':'Auf deinem Gerät entsperren', 'No confirmed cause':'Keine bestätigte Ursache', 'Checking notification settings':'Mitteilungseinstellungen werden geprüft', 'Battery health':'Akkuzustand', 'Health & history':'Zustand & Verlauf', 'Readings unavailable':'Messwerte nicht verfügbar', 'Reading battery state':'Akkustatus wird gelesen', 'Couldn’t apply the limit':'Ladegrenze nicht übernommen', 'Retry limit':'Ladegrenze erneut anwenden', 'Charge cycles':'Ladezyklen', 'Temperature':'Temperatur', 'Estimated capacity':'Geschätzte Kapazität', 'Unavailable':'Nicht verfügbar', 'Recent sessions':'Letzte Ladevorgänge', 'No recorded sessions':'Keine Ladevorgänge aufgezeichnet', 'Preview export':'Exportvorschau', 'Export preview':'Exportvorschau', 'Create sample export':'Beispielexport erstellen', 'Export simulated. No file was written.':'Export simuliert. Es wurde keine Datei geschrieben.', 'Update available':'Update verfügbar', 'Checking for updates':'Updates werden gesucht', 'You’re up to date':'Dein System ist aktuell', 'Couldn’t check for updates':'Updates konnten nicht gesucht werden', 'Check again':'Erneut prüfen', 'Package details':'Paketdetails', 'Downloading':'Wird heruntergeladen', 'Verifying':'Wird überprüft', 'Installing':'Wird installiert', 'Ready to restart':'Bereit zum Neustart', 'Restart to finish':'Zum Abschließen neu starten', 'Restart now':'Jetzt neu starten', 'Later':'Später', 'Couldn’t verify the download':'Download konnte nicht überprüft werden', 'Download a fresh copy':'Neue Kopie herunterladen', 'Update complete':'Update abgeschlossen', 'Current build':'Aktueller Build', 'Target build':'Ziel-Build', 'Device':'Gerät', 'Source':'Quelle', 'Package size':'Paketgröße', 'Signature':'Signatur', 'Share preview':'Teilen-Vorschau', 'Preview sharing':'Teilen ansehen', 'No recipient selected':'Kein Empfänger ausgewählt', 'No file was shared.':'Es wurde keine Datei geteilt.', 'Allow':'Zulassen', 'Don’t allow':'Nicht zulassen', 'Location access':'Standortzugriff', 'Refresh':'Aktualisieren', 'Return to app':'Zur App zurückkehren'
  });

  function fresh(c) {
    return {...baseFresh(c),journey:'home',revealed:false,answer:[],setupError:false,
      preset:'Standard',selectedPreset:'Standard',privacyResult:null,limitError:false,
      recovered:new Set(),modal:null,modalFrom:'',testReceived:false,workStopped:false,
      updateStage:'available',updateProgress:0,updateRetried:false,timers:[],grant:false};
  }
  function title(s) {
    return ({setup:'Setup',review:'Your passphrase',practice:'Practise your phrase',setupDone:'Practice complete',accessibility:'Language & accessibility',presets:'Choose a preset',privacyPreview:'Review changes',controls:'Individual controls',diagnosis:'Find out why',profile:'Work profile',health:'Battery health',updateDetails:'Package details'})[s.page]||baseTitle(s);
  }
  const txt = value => esc(t(value));
  const publicWords = ['orchard','velvet','river','copper','lantern','meadow'];
  const wordOrder = ['copper','orchard','meadow','velvet','lantern','river'];
  function page(s,parent,heading,body) { return `${bar(s,parent)}<h3 class="page-title">${txt(heading)}</h3>${body}`; }
  function actions(...items) { return `<div class="actions">${items.join('')}</div>`; }
  function note(heading,copy,actionLabel='',actionKey='',isError=false) {
    return `<div class="notice${isError?' error':''}" ${isError?'role="alert"':'role="status"'}><strong>${txt(heading)}</strong><p>${txt(copy)}</p>${actionLabel?button(actionLabel,actionKey,'action secondary'):''}</div>`;
  }
  function facts(items) { return `<dl class="state-list">${items.map(([k,v])=>`<div><dt>${txt(k)}</dt><dd>${txt(v)}</dd></div>`).join('')}</dl>`; }
  function appContext(s) { return `<div class="app-context"><span class="app-icon meadow">${icon('messages-square')}</span><div><strong>Meadow</strong><p>${txt(s.workStopped&&['diagnosis','profile'].includes(s.page)?'Work profile':'Personal profile')}</p></div></div>`; }
  function readGate(s,key,loading,unavailable,emptyTitle='') {
    if(s.recovered.has(key))return '';
    if(config.scenario==='loading')return note(loading,'You can leave this screen while the reading is pending.','Refresh','read:'+key);
    if(config.scenario==='unavailable')return note(unavailable,'The current state is unknown. No change has been made.','Try again','read:'+key,true);
    if(config.scenario==='empty'&&emptyTitle)return empty(emptyTitle,'Return when a target is available.','circle-help');
    return '';
  }
  function setupPage(s) {
    if(s.page==='setup')return `<div class="journey-body"><div class="welcome-mark">${icon('scan-face')}</div><h3 class="welcome-title">${txt('Welcome to your phone')}</h3><p class="lead">${txt('A little setup. Then it’s yours.')}</p><ul class="fact-list"><li>${icon('key-round')}<span>A strong passphrase protects your phone.</span></li><li>${icon('user-round')}<span>No DiamaneOS account is needed.</span></li></ul>${actions(button('Start setup','go:review'),button('Language & accessibility','go:accessibility','action secondary'))}</div>`;
    if(s.page==='accessibility')return page(s,'Setup','Language & accessibility',`<p class="lead">Choose what is comfortable before reviewing your phrase.</p><div class="settings-group">${row('English','','languages','language:en')}${row('German draft','','languages','language:de')}${row('Text 100%','','type','size:100')}${row('Text 150%','','type','size:150')}${row('Text 200%','','type','size:200')}${toggle(s,'Reduce motion','reducedMotion')}</div>`);
    if(s.page==='setupDone')return `<div class="journey-body"><div class="success-mark">${icon('check')}</div><h3 class="page-title">${txt('Practice complete')}</h3><p class="lead">You reviewed all six words in order.</p><p class="sample-warning">This preview did not create a credential. Never use these public words to protect a device.</p>${facts([['App privacy','Standard'],['Profile','Personal']])}${actions(button('Go to Home','home'))}</div>`;
    const gate=readGate(s,'setup','Preparing your phrase','Phrase unavailable','No phrase to confirm');
    if(gate)return page(s,'Setup','Your passphrase',gate);
    const progress=`<div class="step-progress" aria-hidden="true"><span class="complete"></span><span class="${s.page==='practice'?'complete':''}"></span><span></span></div>`;
    if(s.page==='review')return `<div class="journey-body">${bar(s,'Setup')}${progress}<h3 class="page-title">${txt('Review your phrase')}</h3><p class="lead">Use your passphrase after a restart and whenever your phone requires strong authentication.</p><p class="sample-warning">Public example only. Never use these words as a real credential.</p>${s.revealed?`<ol class="phrase-grid" aria-label="Public sample phrase">${publicWords.map((w,i)=>`<li><small>${i+1}</small><strong>${w}</strong></li>`).join('')}</ol>${button('Hide words','hide-words','text-action')}`:`<div class="phrase-hidden">${icon('eye-off')}${button('Show sample words','reveal-words','action secondary')}</div>`}<p class="subtle">Fingerprint can be optional where supported. It does not replace required passphrase entry. If you lose the passphrase, your locked data cannot be recovered through a DiamaneOS account.</p>${actions(`<button class="action" data-action="go:practice" ${s.revealed?'':'disabled'}>${txt('Continue to practice')}</button>`)}</div>`;
    return page(s,'Setup','Practise your phrase',`${progress}<p class="lead">Select the six public words in the order you reviewed.</p><p class="step-caption" role="status">${s.answer.length} / 6 words selected</p><div class="phrase-answer" aria-label="Selected public words">${s.answer.length?s.answer.map(esc).join(' · '):'<span class="subtle">Your selection appears here</span>'}</div>${s.setupError?note(s.setupError==='commit'?'Couldn’t finish setup':'That order doesn’t match',s.setupError==='commit'?'Nothing has been set. Try again.':'Review the words or try the full phrase again.','','',true):''}<div class="word-choices">${wordOrder.map(w=>`<button data-action="word:${w}" ${s.answer.includes(w)?'disabled':''}>${w}</button>`).join('')}</div>${actions(`<button class="action" data-action="confirm-phrase" ${s.answer.length===6?'':'disabled'}>${txt(s.setupError==='commit'?'Try again':'Finish practice')}</button>`,button('Undo last word','undo-word','action secondary'),button('Review words','review-words','text-action'))}`);
  }
  function presetTarget(s) {
    return {network:s.selectedPreset!=='Untrusted',sensors:s.selectedPreset==='Trusted'?true:s.sensors};
  }
  function changeRows(s,result=false) {
    const target=presetTarget(s), before=s.privacyResult?.before||{network:s.network,sensors:s.sensors};
    const fields=[['Network access','network'],['Sensor access','sensors']];
    return `<dl class="state-list changes">${fields.map(([label,k])=>{
      const from=before[k]?'Allowed':'Blocked', to=target[k]?'Allowed':'Blocked';
      const failed=result&&s.privacyResult?.failed===k;
      return `<div><dt>${txt(label)}</dt><dd>${failed?`${txt(s[k]?'Allowed':'Blocked')} · Not applied`:from===to?`${txt(from)} · ${txt('No change')}`:`<span class="change-value"><span>${txt(from)}</span>${icon('arrow-right')}<span class="new-value">${txt(to)}</span></span>`}</dd></div>`;
    }).join('')}<div><dt>${txt('Selected files')}</dt><dd>2 folders · ${txt('No change')}</dd></div><div><dt>${txt('Selected contacts')}</dt><dd>3 contacts · ${txt('No change')}</dd></div></dl>`;
  }
  function privacyPage(s) {
    const gate=readGate(s,'privacy','Reading app permissions','Permissions unavailable','No app selected');
    if(gate)return page(s,'Settings','Apps & privacy',gate);
    if(s.page==='presets')return page(s,'Meadow','Choose a preset',`${appContext(s)}<p class="lead">Choose a starting point. Review each change before applying it.</p><fieldset class="choice-list"><legend>${txt('Current preset')}: ${txt(s.preset)}</legend>${[['Untrusted','Block network. Keep other permissions as they are.'],['Standard','Allow network. Keep other permissions as they are.'],['Trusted','Allow network and sensors. This does not mean the app is audited safe.']].map(([name,desc])=>`<label class="choice"><input type="radio" name="preset" data-preset value="${name}" ${s.selectedPreset===name?'checked':''}><span><strong>${name}</strong><small>${txt(desc)}</small></span></label>`).join('')}</fieldset>${actions(`<button class="action" data-action="go:privacyPreview" ${s.selectedPreset?'':'disabled'}>${txt('Review changes')}</button>`)}`);
    if(s.page==='privacyPreview')return page(s,'Meadow','Review changes',`${appContext(s)}<p class="lead">${txt(s.preset)} → ${txt(s.selectedPreset)}</p>${s.privacyResult?.failed?note('Some changes weren’t applied','The failed permission is unchanged. Review the actual result below.','','',true):''}${changeRows(s,Boolean(s.privacyResult))}${actions(button(s.privacyResult?.failed?'Retry failed change':'Apply changes',s.privacyResult?.failed?'retry-preset':'apply-preset'),button(s.privacyResult?.failed?'Return to app':'Cancel',s.privacyResult?.failed?'return-privacy':'cancel-preset','action secondary'))}`);
    if(s.page==='controls')return page(s,'Meadow','Individual controls',`${appContext(s)}<p class="lead">Each control applies only to this app in this profile.</p><div class="settings-group">${toggle(s,'Network access','network',s.network?'Allowed':'Blocked')}${toggle(s,'Sensor access','sensors',s.sensors?'Allowed':'Blocked')}</div>${facts([['Selected files','2 selected folders'],['Selected contacts','3 selected contacts']])}<p class="subtle">Selections stay scoped. A preset never grants all files or contacts.</p>`);
    return page(s,'Settings','Apps & privacy',`${appContext(s)}<div class="preset-summary"><strong>${txt(s.preset)}</strong><p>Only the permissions you allow, for this profile.</p>${button('Change preset','choose-preset','text-action')}</div>${facts([['Network access',s.network?'Allowed':'Blocked'],['Sensor access',s.sensors?'Allowed':'Blocked'],['Selected files','2 selected folders'],['Selected contacts','3 selected contacts']])}${button('Individual controls','go:controls','recovery-link')}`);
  }
  function notificationPage(s) {
    if(s.page==='profile')return page(s,'Notifications','Work profile',`${appContext(s)}<div class="cause"><h4>${txt('Work is stopped')}</h4><p>Work apps cannot receive notifications while this profile is stopped. Its data stays separate from Personal.</p></div>${actions(button('Unlock on your device','profile-handoff'),button('Return to Personal','home','action secondary'))}`);
    const gate=readGate(s,'diagnosis','Checking notification settings','No confirmed cause');
    if(s.page==='notifications')return page(s,'Settings','Notifications',`${appContext({...s,workStopped:false})}<div class="settings-group">${toggle(s,'Meadow notifications','notifications')}</div>${button('Find out why','go:diagnosis','recovery-link')}<p class="subtle">Applies to Meadow in the Personal profile. Delivery depends on the app and its supported connection.</p>`);
    if(gate)return page(s,'Notifications','Find out why',`${appContext(s)}${gate}${button('App notifications','go:notifications','text-action')}`);
    if(s.workStopped)return page(s,'Notifications','Find out why',`${appContext(s)}<div class="cause"><h4>${txt('Work is stopped')}</h4><p>Meadow belongs to Work. Changing Personal settings will not start this profile.</p></div>${actions(button('Open profile settings','go:profile'))}`);
    if(s.testReceived)return page(s,'Notifications','Find out why',`${appContext(s)}<div class="success-mark">${icon('check')}</div><h3 class="page-title">${txt('Test notification received')}</h3><p class="lead">Meadow’s sample notification arrived in Personal.</p><p class="subtle">A real app must provide a supported test or receive an event you trigger. Enabling permission alone does not confirm delivery.</p>${actions(button('Open notifications','go:shade'))}`);
    return page(s,'Notifications','Find out why',`${appContext(s)}<div class="cause"><h4>${txt(s.notifications?'Permission is on':'Notifications are off')}</h4><p>${s.notifications?'Delivery has not been confirmed yet. Try a sample notification.':'Meadow is not allowed to show notifications in Personal. Other apps are unaffected.'}</p></div>${actions(button(s.notifications?'Send sample test':'Turn on notifications',s.notifications?'test-notification':'enable-notifications'))}`);
  }
  function batteryPage(s) {
    if(s.page==='health'&&config.scenario==='unavailable'&&!s.recovered.has('health'))return page(s,'Battery','Battery health',note('Readings unavailable','The current battery readings could not be read. Missing values are not zero.','Try again','read:health',true));
    if(s.page==='health')return page(s,'Battery','Battery health',`<p class="lead">Readings from this battery. Missing values stay visible.</p>${facts([['Charge cycles','198'],['Temperature','31 °C'],['Estimated capacity','Unavailable']])}<p class="reading-note">${icon('clock-3')}<span>Sample readings · just now. Capacity estimation is not available from this source.</span></p><h4 class="section-label">${txt('Recent sessions')}</h4>${config.scenario==='empty'?empty('No recorded sessions','There is nothing to export yet.','battery'):facts([['Today','52% → 80%'],['Yesterday','34% → 80%'],['8 September','41% → 80%']])}<p class="subtle">This example covers three sessions with the current battery. A replacement or reset starts a new scope.</p><button class="action" data-action="export" ${config.scenario==='empty'?'disabled':''}>${txt('Preview export')}</button>`);
    const gate=readGate(s,'battery','Reading battery state','Readings unavailable');
    return page(s,'Settings','Battery',`${gate?gate:`<div class="battery-summary">${icon('battery-charging')}<div><strong>76%</strong><p>${txt('Charging')} · just now</p></div></div><div class="limit-panel"><h4>${txt('Charge limit')}</h4><p class="subtle">Choose a limit for everyday charging.</p><label class="limit-value" for="charge-limit">${txt('Requested')}<output data-limit-value>${s.wanted}%</output></label><input id="charge-limit" type="range" data-limit min="70" max="100" step="5" value="${s.wanted}"><p class="subtle">${txt('Last confirmed')}: <strong data-confirmed>${s.confirmed}%</strong></p>${s.limitError?note('Couldn’t apply the limit','The confirmed limit has not changed.','','',true):''}${button(s.limitError?'Retry limit':'Apply limit',s.limitError?'retry-limit':'apply-limit')}</div><p class="reading-note">${icon('info')}<span>The confirmed limit is the last accepted request. Actual charging may differ.</span></p>`}<div class="settings-group">${row('Health & history','Cycles, temperature, recent sessions','heart-pulse','go:health')}</div>`);
  }
  function updatePage(s) {
    if(s.page==='updateDetails')return page(s,'System update','Package details',`${facts([['Device','Fairphone 6'],['Source','DiamaneOS preview channel'],['Current build',s.updateStage==='complete'?'Preview 02':'Preview 01'],['Target build','Preview 02'],['Package size','428 MB'],['Signature',['ready','complete'].includes(s.updateStage)?'Verified in this sample':'Checked before installation']])}<p class="subtle">All package identities are fictional. A real updater must match device, source build, trusted signer, and rollback policy before installation.</p>`);
    const gate=readGate(s,'updates','Checking for updates','Couldn’t check for updates');
    if(gate)return page(s,'Settings','System update',`${gate}<p class="subtle">${txt('Current build')}: Preview 01</p>`);
    if(config.scenario==='empty'&&!s.recovered.has('updates-empty'))return page(s,'Settings','System update',`<div class="success-mark">${icon('check')}</div><h3 class="page-title">${txt('You’re up to date')}</h3>${facts([['Current build','Preview 01'],['Last checked','Just now']])}${button('Check again','read:updates-empty')}`);
    const stage=s.updateStage;
    const identity=`<div class="update-identity">${icon('download')}<div><strong>DiamaneOS Preview 02</strong><small>Fairphone 6 · 428 MB</small></div></div>`;
    if(stage==='complete')return page(s,'Settings','System update',`<div class="success-mark">${icon('check')}</div><h3 class="page-title">${txt('Update complete')}</h3>${facts([['Current build','Preview 02']])}<p class="lead">The restart and retained apps are simulated. No update was installed on a device.</p>${actions(button('Go to Home','home'),button('Check again','check-update','action secondary'))}`);
    if(stage==='error')return page(s,'Settings','System update',`${identity}${note('Couldn’t verify the download','The download is damaged. It was rejected before installation. Your current build is still active.','','',true)}${actions(button('Download a fresh copy','retry-update'),button('Package details','go:updateDetails','action secondary'))}`);
    const active=['downloading','verifying','installing'].includes(stage);
    return page(s,'Settings','System update',`${identity}<p class="lead">${txt(stage==='ready'?'Ready to restart':active?{downloading:'Downloading',verifying:'Verifying',installing:'Installing'}[stage]:'Update available')}</p>${active?`<progress class="update-progress" max="100" value="${s.updateProgress}" aria-label="${txt(stage==='downloading'?'Downloading':stage==='verifying'?'Verifying':'Installing')}"></progress><ol class="update-stages">${['downloading','verifying','installing'].map((step,i)=>`<li data-active="${stage===step}">${icon(['downloading','verifying','installing'].indexOf(stage)>i?'check':'circle')}<span>${txt(['Downloading','Verifying','Installing'][i])}</span></li>`).join('')}</ol><p class="subtle">You can leave this screen. The sample update continues.</p>`:stage==='ready'?`<p class="lead">Installation is ready. Restart when you’re ready to finish.</p><p class="subtle">Your passphrase will be required after restart.</p>`:`<p class="details-copy">This sample package is checked before installation. Your current build remains active until restart.</p>${facts([['Current build','Preview 01']])}`}${actions(active?button('Later','home','action secondary'):stage==='ready'?button('Restart to finish','restart'):button('Download update','start-update'),button('Package details','go:updateDetails','action secondary'))}`);
  }
  function support(s) {
    if(['setup','review','practice','setupDone','accessibility'].includes(s.page))return setupPage(s);
    if(['privacy','presets','privacyPreview','controls'].includes(s.page))return privacyPage(s);
    if(['notifications','diagnosis','profile'].includes(s.page))return notificationPage(s);
    if(['battery','health'].includes(s.page))return batteryPage(s);
    if(['updates','updateDetails'].includes(s.page))return updatePage(s);
    if(s.page==='search') {
      const gate=readGate(s,'search','Searching','Search unavailable');
      return `${bar(s,'Home')}${gate||`${searchField('Search apps and settings',s.query)}<div class="results">${searchResults(s)}</div>`}${gate?actions(button('Files','go:files','action secondary'),button('Settings','go:settings','action secondary')):''}`;
    }
    if(s.page==='atlas')return `${bar(s,'Home')}<h3 class="page-title">Atlas</h3><p class="lead">Find nearby trails</p>${facts([['Location access',s.grant?'Approximate location':'Not requested']])}${button('Find nearby trails','location')}`;
    if(s.page==='file'||s.page==='notes')return baseSupport(s)+button('Preview sharing','share','recovery-link');
    return baseSupport(s);
  }
  function shade(s) {
    let html=baseShade(s);
    if(!s.testReceived&&!s.notifications)html=html.replace(notification(s,'meadow'),'');
    if(s.testReceived&&!s.dismissed.includes('test')&&config.scenario!=='empty')html=html.replace('<p class="section-label">',`<div class="notification"><div class="notification-top">${icon('messages-square')}<span>Meadow · Personal</span>${ib('Dismiss','dismiss:test','x')}</div><button class="notification-open" data-action="go:diagnosis"><strong>${txt('Test notification received')}</strong><span>This sample arrived successfully.</span></button></div><p class="section-label">`);
    return html.replace('<button class="close-shade"',`<button class="recovery-link" data-action="go:diagnosis">${icon('bell')}<span>${txt('Missing notifications?')}</span>${icon('chevron-right')}</button><button class="close-shade"`);
  }
  function openModal(s,name,from) { s.modal=name;s.modalFrom=from;render(s,{focus:'modal-close'}); }
  function closeModal(s) { const from=s.modalFrom;s.modal=null;render(s,{focus:from}); }
  function renderModal(s) {
    if(!s.modal)return;
    const content={
      restart:['Restart to finish','Your passphrase is required after restart. The sample restart does not restart this computer or a phone.',actions(button('Restart now','confirm-restart'),button('Later','modal-close','action secondary'))],
      export:['Export preview','Sample fields: session date, start charge, end charge, and battery scope. No app usage or identifiers are included.',facts([['Scope','Current sample battery · 3 sessions'],['Destination','Preview only']])+actions(button('Create sample export','confirm-export'),button('Cancel','modal-close','action secondary'))],
      location:['Location access','Allow Atlas in Personal to use your approximate location?',facts([['Precision','Approximate location']])+actions(button('Allow','allow-location','action secondary'),button('Don’t allow','deny-location','action secondary'))],
      share:['Share preview',`${s.page==='notes'?'Notes.txt':'Weekend itinerary.pdf'} · one sample file. Choose a recipient in the native share sheet.`,facts([['Recipient','No recipient selected']])+actions(button('Close','modal-close','action secondary'))],
      profile:['Unlock on your device','A real device must authenticate the Work profile through its own credential screen. This preview cannot unlock it.',actions(button('Close','modal-close','action secondary'))]
    }[s.modal];
    const p=phone(s);p.querySelector('.screen').inert=true;p.querySelector('.statusbar').inert=true;p.querySelector('.nav-bar').inert=false;
    p.querySelector('.viewport').insertAdjacentHTML('beforeend',`<div class="sheet-layer"><section class="sheet" role="dialog" aria-modal="true" aria-labelledby="dialog-title" aria-describedby="dialog-copy"><div class="toolbar"><h3 id="dialog-title">${txt(content[0])}</h3>${ib('Close','modal-close','x')}</div><p id="dialog-copy">${txt(content[1])}</p>${content[2]}</section></div>`);
    paintIcons(p);p.querySelector('.sheet [data-action]').focus({preventScroll:true});
  }
  function route(s,dest) {
    s.modal=null;
    if(dest==='practice'){s.answer=[];s.setupError=false;s.revealed=false;}
    if(dest==='privacyPreview')s.privacyResult=null;
    return baseRoute(s,dest);
  }
  function back(s) {
    if(s.modal)return closeModal(s);
    if(['review','practice'].includes(s.page))s.revealed=false;
    baseBack(s);
  }
  function startUpdate(s,retry=false) {
    if(['downloading','verifying','installing'].includes(s.updateStage))return;
    s.updateRetried=retry;s.updateStage='downloading';s.updateProgress=0;render(s,{focus:'screen'});
    const advance=()=>{
      const previousStage=s.updateStage;
      s.updateProgress+=25;
      if(s.updateProgress>=100){
        s.updateProgress=0;
        if(s.updateStage==='downloading')s.updateStage='verifying';
        else if(s.updateStage==='verifying')s.updateStage=config.scenario==='failure'&&!s.updateRetried?'error':'installing';
        else if(s.updateStage==='installing')s.updateStage='ready';
      }
      if(s.page==='updates'){
        if(previousStage!==s.updateStage)render(s);
        else {const progress=phone(s).querySelector('.update-progress');if(progress)progress.value=s.updateProgress;}
      }
      else phone(s).querySelectorAll('[data-action="go:updates"] .row-copy small').forEach(el=>{el.textContent=t(s.updateStage==='ready'?'Ready to restart':s.updateStage==='error'?'Couldn’t verify the download':'Update in progress');});
      if(['downloading','verifying','installing'].includes(s.updateStage))s.timers.push(setTimeout(advance,250));
    };
    s.timers.push(setTimeout(advance,250));
  }
  function applyPreset(s,retry=false) {
    const target=presetTarget(s),before={network:s.network,sensors:s.sensors};
    let failed=null;
    for(const key of ['network','sensors']) {
      if(s[key]===target[key])continue;
      if(config.scenario==='failure'&&!retry&&failed===null)failed=key;
      else s[key]=target[key];
    }
    s.privacyResult={before,failed};
    if(failed)return render(s,{focus:'retry-preset'});
    s.preset=s.selectedPreset;s.page='privacy';s.stack=s.stack.filter(x=>!['presets','privacyPreview'].includes(x.page));s.toast='Changes applied';render(s,{focus:'choose-preset',restoreScroll:0});
  }
  function action(s,key) {
    if(key==='home'){s.modal=null;s.revealed=false;s.answer=[];s.setupError=false;}
    if(key==='modal-close')return closeModal(s);
    if(key==='reveal-words'||key==='hide-words'){s.revealed=key==='reveal-words';return render(s,{focus:s.revealed?'hide-words':'reveal-words'});}
    if(key.startsWith('word:')){const word=key.slice(5);if(!s.answer.includes(word)&&s.answer.length<6)s.answer.push(word);s.setupError=false;return render(s,{focus:s.answer.length===6?'confirm-phrase':'word:'+wordOrder.find(w=>!s.answer.includes(w))});}
    if(key==='undo-word'){s.answer.pop();s.setupError=false;return render(s,{focus:key});}
    if(key==='review-words'){s.revealed=false;return back(s);}
    if(key==='confirm-phrase'){
      if(s.answer.join(' ')!==publicWords.join(' ')){s.setupError=true;s.answer=[];return render(s,{focus:'review-words'});}
      if(config.scenario==='failure'&&!s.recovered.has('setup')){s.setupError='commit';s.recovered.add('setup');return render(s,{focus:'confirm-phrase'});}
      s.revealed=false;s.answer=[];return route(s,'setupDone');
    }
    if(key.startsWith('language:')){config.locale=key.slice(9);document.getElementById('locale').value=config.locale;return render(s,{focus:key});}
    if(key.startsWith('size:')){const size=key.slice(5);config.scale=Number(size)/100;document.getElementById('text').value=size;return render(s,{focus:key});}
    if(key==='toggle:reducedMotion'){config.reduced=!config.reduced;s.reducedMotion=config.reduced;document.getElementById('reduced').checked=config.reduced;return render(s,{focus:key});}
    if(key.startsWith('read:')){s.recovered.add(key.slice(5));return render(s,{focus:'screen'});}
    if(key==='choose-preset'){s.selectedPreset=s.preset==='Custom'?'':s.preset;s.privacyResult=null;return route(s,'presets');}
    if(key==='apply-preset'||key==='retry-preset')return applyPreset(s,key==='retry-preset');
    if(key==='return-privacy'){s.page='privacy';s.privacyResult=null;s.stack=s.stack.filter(x=>!['presets','privacyPreview'].includes(x.page));return render(s,{focus:'choose-preset',restoreScroll:0});}
    if(key==='cancel-preset'){s.privacyResult=null;return back(s);}
    if(['toggle:network','toggle:sensors'].includes(key))s.preset='Custom';
    if(key==='enable-notifications'){s.notifications=true;return render(s,{focus:'test-notification'});}
    if(key==='test-notification'){s.testReceived=true;s.dismissed=s.dismissed.filter(x=>x!=='test');return render(s,{focus:'screen'});}
    if(key==='profile-handoff')return openModal(s,'profile',key);
    if(key==='apply-limit'||key==='retry-limit'){
      if(config.scenario==='failure'&&key!=='retry-limit'){s.limitError=true;return render(s,{focus:'retry-limit'});}
      s.limitError=false;s.confirmed=s.wanted;s.toast='Limit acknowledged';return render(s,{focus:'apply-limit'});
    }
    if(key==='export'||key==='share'||key==='location'||key==='restart')return openModal(s,key,key);
    if(key==='confirm-export'){s.modal=null;s.toast='Export simulated. No file was written.';return render(s,{focus:'export'});}
    if(key==='allow-location'||key==='deny-location'){s.grant=key==='allow-location';s.modal=null;s.toast=s.grant?'Approximate location allowed in this sample':'Location blocked in this sample';return render(s,{focus:'location'});}
    if(key==='start-update'||key==='retry-update')return startUpdate(s,key==='retry-update');
    if(key==='confirm-restart'){s.modal=null;s.updateStage='complete';return render(s,{focus:'screen',restoreScroll:0});}
    if(key==='check-update'){s.recovered.add('updates');s.toast='No newer sample update';return render(s,{focus:'screen'});}
    return baseAction(s,key);
  }
  function resetJourney(s,journey=s.journey) {
    s.timers.forEach(clearTimeout);s.animation?.cancel();Object.assign(s,fresh(concepts[0]));delete s.theme;
    s.journey=journey;s.page=journey==='notifications'?'shade':journey;s.reducedMotion=config.reduced;
    if(journey==='notifications'){s.notifications=false;s.workStopped=config.scenario==='failure';}
    s.stack=['home','setup'].includes(s.page)?[]:[{page:'home',query:'',scroll:0,focus:''}];
    document.querySelectorAll('[data-journey]').forEach(b=>{if(b.dataset.journey===journey)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
    document.getElementById('journey-guide').textContent=journeyGuides[journey];render(s,{restoreScroll:0});
  }

  const grid=document.getElementById('concepts');grid.innerHTML='<div class="phone" id="phone-lumen" data-concept="lumen" role="region" aria-label="Lumen phone"></div>';
  states.forEach(s=>render(s));
  let swipe=null,suppressedSwipe=null;
  grid.addEventListener('click',e=>{const sw=e.target.closest('[data-swipe]');if(suppressedSwipe&&sw?.dataset.swipe===suppressedSwipe.kind&&sw.closest('.phone')?.id===suppressedSwipe.phone&&performance.now()<suppressedSwipe.until){suppressedSwipe=null;e.preventDefault();return;}const target=e.target.closest('[data-action]'),p=target?.closest('.phone');if(p)action(states.find(s=>'phone-'+s.id===p.id),target.dataset.action);});
  grid.addEventListener('input',e=>{const p=e.target.closest('.phone');if(!p)return;const s=states.find(s=>'phone-'+s.id===p.id);if(e.target.hasAttribute('data-query')){s.query=e.target.value;p.querySelector('.results').innerHTML=s.page==='settings'?settingsResults(s):searchResults(s);paintIcons(p);}if(e.target.hasAttribute('data-bright')){s.bright=Number(e.target.value);e.target.nextElementSibling.textContent=s.bright+'%';}if(e.target.hasAttribute('data-limit')){s.wanted=Number(e.target.value);p.querySelector('[data-limit-value]').textContent=s.wanted+'%';}});
  grid.addEventListener('keydown',e=>{if(e.key==='Escape'){const p=e.target.closest('.phone');if(p){e.preventDefault();back(states.find(s=>'phone-'+s.id===p.id));}}});
  grid.addEventListener('pointerdown',e=>{const target=e.target.closest('[data-swipe]');if(!target||e.button!==0||!e.isPrimary)return;if(e.target.closest('input')||e.target.closest('button')&&e.target.closest('button')!==target)return;swipe={target,x:e.clientX,y:e.clientY,id:e.pointerId};target.setPointerCapture?.(e.pointerId);});
  grid.addEventListener('pointercancel',()=>{swipe=null;});
  grid.addEventListener('pointerup',e=>{if(!swipe||e.pointerId!==swipe.id)return;const sw=swipe;swipe=null;const dx=e.clientX-sw.x,dy=e.clientY-sw.y;if(Math.abs(dy)<40||Math.abs(dy)<Math.abs(dx)*1.4)return;const s=states.find(s=>'phone-'+s.id===sw.target.closest('.phone').id);suppressedSwipe={kind:sw.target.dataset.swipe,phone:sw.target.closest('.phone').id,until:performance.now()+300};if(sw.target.dataset.swipe==='status'&&dy>0&&s.page!=='shade')route(s,'shade');else if(sw.target.dataset.swipe==='shade'&&dy<0)back(s);else if(s.page==='home'&&dy<0)route(s,'apps');else if(s.page==='home'&&dy>0)route(s,'search');});
  document.querySelectorAll('[data-journey]').forEach(b=>b.addEventListener('click',()=>resetJourney(states[0],b.dataset.journey)));
  grid.addEventListener('change',e=>{if(e.target.matches('[data-preset]')){states[0].selectedPreset=e.target.value;states[0].privacyResult=null;const review=grid.querySelector('[data-action="go:privacyPreview"]');if(review)review.disabled=false;}});
  grid.addEventListener('keydown',e=>{const dialog=grid.querySelector('[role="dialog"]');if(!dialog||e.key!=='Tab')return;const all=Array.from(dialog.querySelectorAll('button:not(:disabled),input:not(:disabled),a[href]'));const first=all[0],last=all[all.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}});
  document.getElementById('theme').addEventListener('change',e=>{config.theme=e.target.value;states.forEach(s=>{delete s.theme;render(s);});});
  document.getElementById('text').addEventListener('change',e=>{config.scale=Number(e.target.value)/100;states.forEach(s=>render(s));});
  document.getElementById('locale').addEventListener('change',e=>{config.locale=e.target.value;states.forEach(s=>render(s));});
  document.getElementById('reduced').addEventListener('change',e=>{config.reduced=e.target.checked;states.forEach(s=>{s.reducedMotion=config.reduced;render(s);});});
  document.getElementById('scenario').addEventListener('change',e=>{config.scenario=e.target.value;resetJourney(states[0]);});
  document.getElementById('reset').addEventListener('click',()=>resetJourney(states[0]));
  window.addEventListener('pagehide',()=>states.forEach(s=>{s.timers.forEach(clearTimeout);s.animation?.cancel();}));
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>{states.filter(s=>config.theme==='system'||s.theme==='system').forEach(s=>render(s));});
})();
