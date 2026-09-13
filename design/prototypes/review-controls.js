/* Optional controls for the unchanged baseline's existing Tweak integration. */
window.Tweak=class {
  constructor({container,onChange}) {
    this.onChange=onChange;
    this.panel=document.createElement('fieldset');
    this.panel.style.cssText='display:flex;flex-wrap:wrap;gap:12px;margin:0 0 20px;padding:16px;border:1px solid #747980;border-radius:12px;font:13px system-ui;color:CanvasText;background:Canvas';
    const legend=document.createElement('legend');legend.textContent='Baseline review controls';this.panel.append(legend);
    container.prepend(this.panel);
  }
  addSelect(object,key,{label,options}) {
    const input=document.createElement('select');
    options.forEach(({label,value})=>input.add(new Option(label,value)));
    input.value=object[key];this.field(input,label,()=>{object[key]=input.value;this.onChange()});
  }
  addToggle(object,key,{label}) {
    const input=document.createElement('input');input.type='checkbox';input.checked=object[key];
    this.field(input,label,()=>{object[key]=input.checked;this.onChange()});
  }
  addSlider(object,key,{label,min,max,step,unit}) {
    const input=document.createElement('select');
    for(let value=min;value<=max;value+=step)input.add(new Option(value+(unit||''),value));
    input.value=object[key];this.field(input,label,()=>{object[key]=Number(input.value);this.onChange()});
  }
  field(input,title,change) {
    const label=document.createElement('label');label.style.cssText='display:flex;gap:6px;align-items:center;flex-wrap:wrap';
    label.append(document.createTextNode(title+' '),input);input.setAttribute('aria-label',title);
    input.style.cssText='font:inherit;min-height:36px;max-width:100%;color:CanvasText;background:Canvas';input.addEventListener('change',change);this.panel.append(label);
  }
};
