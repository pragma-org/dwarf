/* DWARF Basic view — generic reducer. Runs ONLY under body[data-ui="basic"].
 * Never removes data; caps the visible set and links the full view in Advanced.
 * Pages with bespoke Basic markup (.basic-only) are left alone. */
(function () {
  if (document.body.getAttribute("data-ui") !== "basic") return;
  if (document.querySelector(".basic-only")) return;
  var path = location.pathname || "/";
  var advHref = path + "?view=advanced";
  var main = document.querySelector(".shell-main") || document.querySelector("main");
  if (!main) return;
  var isLearn = /^\/learn\//.test(path);

  function link(label){var a=document.createElement("a");a.className="b-adv-link b-reduce-more";
    a.href=advHref;a.textContent=label+" →";return a;}
  function cap(nodes, keep, label, anchor){
    if(!nodes||nodes.length<=keep) return false;
    for(var i=keep;i<nodes.length;i++) nodes[i].classList.add("is-basic-capped");
    (anchor||nodes[keep-1]).insertAdjacentElement("afterend", link(label));
    return true;
  }
  var ADV = isLearn ? "Read the full reference in Advanced" : "Open the full view in Advanced";

  // 1) Primary collection (catalogue/index): cap rows.
  var collSels=["table.runs-table tbody","table.asset-catalog-table tbody","table.config-table tbody",
    "main table tbody",".learn-tile-grid","[data-version-list]","[data-profile-list]",
    "ul.scenario-list","[data-testcase-grid]","[data-bucket-grid]"];
  for(var s=0;s<collSels.length;s++){var el=document.querySelector(collSels[s]);if(!el)continue;
    var rs=el.matches("tbody")?":scope > tr":el.matches(".learn-tile-grid")?":scope > .learn-tile, :scope > a":
      el.matches("ul.scenario-list")?":scope > li":":scope > *";
    var rows=el.querySelectorAll(rs);
    if(rows.length>8){cap(rows,8,"Show all "+rows.length+" in Advanced",el.closest("table")||el);return;}
  }

  // 2) Top-level sections (detail/forms/misc/reference with real sections).
  var secs=main.querySelectorAll("section"), top=[];
  secs.forEach(function(sec){for(var i=0;i<top.length;i++)if(top[i].contains(sec))return;top.push(sec);});
  if(cap(top, isLearn?2:3, ADV)) return;

  // 3) Top-level disclosures.
  if(cap(main.querySelectorAll("details"), 2, ADV)) return;

  // 4) Long definition list (glossary-style): cap dt/dd pairs.
  var dl=main.querySelector("dl");
  if(dl){var kids=dl.children, pairs=Math.floor(kids.length/2);
    if(pairs>10){for(var k=20;k<kids.length;k++) kids[k].classList.add("is-basic-capped");
      dl.insertAdjacentElement("afterend", link("Show all "+pairs+" terms in Advanced")); return;}}

  // 5) Long prose (reference pages that are flat paragraphs): keep the lead.
  var flow=main.querySelectorAll(":scope > p, :scope > h2, :scope > h3, :scope > ul, :scope > pre, :scope > blockquote");
  if(flow.length>10){
    // keep up to the 2nd h2 (or first 8 flow nodes), cap the rest
    var keptTo=0,h2seen=0;
    for(var f=0;f<flow.length;f++){if(flow[f].tagName==="H2"){h2seen++;if(h2seen>1){keptTo=f;break;}}}
    if(!keptTo) keptTo=Math.min(8,flow.length);
    for(var g=keptTo;g<flow.length;g++) flow[g].classList.add("is-basic-capped");
    flow[keptTo-1].insertAdjacentElement("afterend", link(ADV)); return;
  }

  // 6) Repeated-block fallback: find the largest group of sibling blocks that
  //    share a tag+first-class (concept cards, form field-groups, steps, list
  //    items rendered as articles) and cap beyond a small lead.
  var best=null, bestN=0;
  var containers=[main]; main.querySelectorAll("section, .learn-walkthrough, .definition-editor, form, div").forEach(function(c){containers.push(c);});
  containers.forEach(function(c){
    var groups={};
    for(var i=0;i<c.children.length;i++){var ch=c.children[i];
      if(/^(SCRIPT|STYLE|NAV|H1|HEADER)$/.test(ch.tagName)) continue;
      var key=ch.tagName+"."+(ch.className?String(ch.className).split(" ")[0]:"");
      (groups[key]=groups[key]||[]).push(ch);
    }
    for(var k in groups){ if(groups[k].length>bestN){bestN=groups[k].length; best=groups[k];} }
  });
  if(best && bestN>=6){
    var keep=3;
    for(var b=keep;b<best.length;b++) best[b].classList.add("is-basic-capped");
    best[keep-1].insertAdjacentElement("afterend", link(ADV));
    return;
  }
})();