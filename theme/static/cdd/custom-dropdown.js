/* Fac-Check Custom Dropdown (CDD) v1
   Opt-in: <select data-cdd>
   Opsyonal na attributes:
     data-cdd-size="lg"            malaking field (report form)
     data-cdd-variant="transparent" walang border/background (signup)
     data-cdd-width="24%"          lapad ng wrapper
     data-cdd-class="..."          dagdag na class sa wrapper
     data-cdd-placeholder="..."    text kapag walang napili
     data-cdd-plain-empty          huwag gawing kulay-placeholder ang option na value=""
   API: CDD.init(root), CDD.refresh(select), CDD.close() */
(function () {
  "use strict";
  if (window.CDD) return;

  var CHEVRON =
    '<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="M19.5 8.25l-7.5 7.5-7.5-7.5" /></svg>';
  var CHECK =
    '<svg class="chk" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2.5" stroke="currentColor" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" d="m4.5 12.75 6 6 9-13.5" /></svg>';

  var valueDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
  var indexDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "selectedIndex");
  var openInst = null;

  function enabledItems(inst) {
    return Array.prototype.slice.call(
      inst.list.querySelectorAll(".cdd-item:not([aria-disabled='true'])")
    );
  }

  function setActive(inst, el) {
    var cur = inst.list.querySelector(".cdd-item.active");
    if (cur) cur.classList.remove("active");
    if (!el) return;
    el.classList.add("active");
    var list = inst.list,
      top = el.offsetTop,
      bottom = top + el.offsetHeight;
    if (top < list.scrollTop) list.scrollTop = top - 6;
    else if (bottom > list.scrollTop + list.clientHeight)
      list.scrollTop = bottom - list.clientHeight + 6;
  }

  function closeOpen(focusBtn) {
    if (!openInst) return;
    var inst = openInst;
    openInst = null;
    inst.list.classList.remove("show");
    inst.btn.classList.remove("open");
    inst.btn.setAttribute("aria-expanded", "false");
    setActive(inst, null);
    if (focusBtn) inst.btn.focus();
  }

  function labelInfo(select) {
    var opt = select.options[select.selectedIndex];
    if (!opt || select.value === "") {
      var empty = Array.prototype.filter.call(select.options, function (o) {
        return o.value === "";
      })[0];
      var text =
        select.getAttribute("data-cdd-placeholder") ||
        (empty && empty.textContent.trim()) ||
        "Select";
      return { text: text, placeholder: !select.hasAttribute("data-cdd-plain-empty") };
    }
    return { text: opt.textContent.trim(), placeholder: false };
  }

  function sync(inst) {
    var select = inst.select,
      info = labelInfo(select),
      sel = select.selectedIndex;
    inst.label.textContent = info.text;
    inst.label.classList.toggle("placeholder", info.placeholder);
    Array.prototype.forEach.call(inst.list.children, function (item) {
      var on = Number(item.dataset.index) === sel;
      item.classList.toggle("selected", on);
      item.setAttribute("aria-selected", on ? "true" : "false");
    });
    var locked = select.hasAttribute("data-cdd-locked");
    inst.btn.disabled = select.disabled;
    inst.btn.classList.toggle("locked", locked);
    inst.btn.setAttribute("aria-disabled", select.disabled || locked ? "true" : "false");
    if ((select.disabled || locked) && openInst === inst) closeOpen(false);
  }

  function build(inst) {
    inst.list.innerHTML = "";
    Array.prototype.forEach.call(inst.select.options, function (opt, idx) {
      if (opt.hidden) return;
      var item = document.createElement("button");
      item.type = "button";
      item.className = "cdd-item";
      item.tabIndex = -1;
      item.setAttribute("role", "option");
      item.dataset.index = String(idx);
      if (opt.disabled) item.setAttribute("aria-disabled", "true");
      item.innerHTML = "<span></span>" + CHECK;
      item.firstChild.textContent = opt.textContent.trim();
      inst.list.appendChild(item);
    });
    sync(inst);
  }

  function open(inst) {
    if (inst.select.disabled || inst.select.hasAttribute("data-cdd-locked")) return;
    closeOpen(false);
    var list = inst.list,
      btn = inst.btn;
    list.classList.remove("up");
    list.classList.add("show");
    btn.classList.add("open");
    btn.setAttribute("aria-expanded", "true");
    openInst = inst;
    var lr = list.getBoundingClientRect(),
      br = btn.getBoundingClientRect();
    if (lr.bottom > window.innerHeight - 8 && br.top > lr.height + 16) list.classList.add("up");
    setActive(
      inst,
      list.querySelector(".cdd-item.selected:not([aria-disabled='true'])") || enabledItems(inst)[0]
    );
  }

  function choose(inst, item) {
    var select = inst.select,
      idx = Number(item.dataset.index);
    var changed = select.selectedIndex !== idx;
    select.selectedIndex = idx;
    closeOpen(true);
    if (changed) {
      select.dispatchEvent(new Event("input", { bubbles: true }));
      select.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function move(inst, dir) {
    var all = enabledItems(inst);
    if (!all.length) return;
    var cur = all.indexOf(inst.list.querySelector(".cdd-item.active"));
    var next =
      cur < 0 ? (dir > 0 ? 0 : all.length - 1) : Math.min(all.length - 1, Math.max(0, cur + dir));
    setActive(inst, all[next]);
  }

  function typeahead(inst, ch) {
    clearTimeout(inst.taTimer);
    inst.ta = (inst.ta || "") + ch.toLowerCase();
    inst.taTimer = setTimeout(function () {
      inst.ta = "";
    }, 600);
    var hit = enabledItems(inst).filter(function (it) {
      return it.textContent.trim().toLowerCase().indexOf(inst.ta) === 0;
    })[0];
    if (!hit) return;
    if (openInst !== inst) open(inst);
    setActive(inst, hit);
  }

  function enhance(select) {
    if (!select || select.multiple || select.getAttribute("data-cdd-enhanced")) return;
    var inst = { select: select };

    var wrap = document.createElement("div");
    wrap.className = "cdd";
    if (select.getAttribute("data-cdd-size") === "lg") wrap.classList.add("cdd-lg");
    if (select.getAttribute("data-cdd-variant") === "transparent")
      wrap.classList.add("cdd-transparent");
    var extra = select.getAttribute("data-cdd-class");
    if (extra)
      extra.split(/\s+/).forEach(function (c) {
        if (c) wrap.classList.add(c);
      });
    var w = select.getAttribute("data-cdd-width");
    if (w) wrap.style.width = w;

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "cdd-btn";
    btn.setAttribute("aria-haspopup", "listbox");
    btn.setAttribute("aria-expanded", "false");
    var ariaLabel = select.getAttribute("aria-label");
    if (ariaLabel) btn.setAttribute("aria-label", ariaLabel);
    var label = document.createElement("span");
    label.className = "cdd-label";
    btn.appendChild(label);
    btn.insertAdjacentHTML("beforeend", CHEVRON);

    var list = document.createElement("div");
    list.className = "cdd-list";
    list.setAttribute("role", "listbox");

    inst.wrap = wrap;
    inst.btn = btn;
    inst.list = list;
    inst.label = label;

    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(btn);
    wrap.appendChild(list);
    wrap.appendChild(select);
    select.setAttribute("data-cdd-enhanced", "1");
    select.setAttribute("aria-hidden", "true");
    select.tabIndex = -1;
    select._cdd = inst;

    // Mahuli ang programmatic na pagbabago (select.value = ..., selectedIndex = ...)
    Object.defineProperty(select, "value", {
      configurable: true,
      get: function () {
        return valueDesc.get.call(this);
      },
      set: function (v) {
        valueDesc.set.call(this, v);
        sync(inst);
      },
    });
    Object.defineProperty(select, "selectedIndex", {
      configurable: true,
      get: function () {
        return indexDesc.get.call(this);
      },
      set: function (v) {
        indexDesc.set.call(this, v);
        sync(inst);
      },
    });

    btn.addEventListener("click", function () {
      if (openInst === inst) closeOpen(false);
      else open(inst);
    });
    btn.addEventListener("keydown", function (e) {
      var isOpen = openInst === inst,
        key = e.key;
      if (key === "ArrowDown" || key === "ArrowUp") {
        e.preventDefault();
        if (!isOpen) open(inst);
        else move(inst, key === "ArrowDown" ? 1 : -1);
      } else if (key === "Home" || key === "End") {
        if (isOpen) {
          e.preventDefault();
          var all = enabledItems(inst);
          setActive(inst, key === "Home" ? all[0] : all[all.length - 1]);
        }
      } else if (key === "Enter" || key === " ") {
        e.preventDefault();
        if (!isOpen) {
          open(inst);
          return;
        }
        var a = inst.list.querySelector(".cdd-item.active");
        if (a) choose(inst, a);
      } else if (key === "Escape") {
        if (isOpen) {
          e.preventDefault();
          e.stopPropagation();
          closeOpen(true);
        }
      } else if (key === "Tab") {
        closeOpen(false);
      } else if (key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
        typeahead(inst, key);
      }
    });
    btn.addEventListener("keyup", function (e) {
      if (e.key === " ") e.preventDefault();
    });

    list.addEventListener("click", function (e) {
      var item = e.target.closest(".cdd-item");
      if (!item || item.getAttribute("aria-disabled") === "true") return;
      choose(inst, item);
    });
    list.addEventListener("mouseover", function (e) {
      var item = e.target.closest(".cdd-item");
      if (item && item.getAttribute("aria-disabled") !== "true") setActive(inst, item);
    });

    select.addEventListener("change", function () {
      wrap.classList.remove("invalid");
      sync(inst);
    });
    select.addEventListener("invalid", function () {
      wrap.classList.add("invalid");
    });
    select.addEventListener("focus", function () {
      btn.focus();
    });
    if (select.form) {
      select.form.addEventListener("reset", function () {
        setTimeout(function () {
          wrap.classList.remove("invalid");
          sync(inst);
        }, 0);
      });
    }

    // Dependent chains / AJAX: kapag nagbago ang options o disabled, mag-rebuild
    var pending = false;
    new MutationObserver(function () {
      if (pending) return;
      pending = true;
      Promise.resolve().then(function () {
        pending = false;
        build(inst);
      });
    }).observe(select, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: ["disabled", "hidden", "selected", "data-cdd-locked"],
    });

    build(inst);
  }

  function init(root) {
    var scope = root && root.querySelectorAll ? root : document;
    if (scope !== document && scope.matches && scope.matches("select[data-cdd]")) enhance(scope);
    Array.prototype.forEach.call(scope.querySelectorAll("select[data-cdd]"), enhance);
  }

  document.addEventListener("click", function (e) {
    if (openInst && !openInst.wrap.contains(e.target)) closeOpen(false);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeOpen(false);
  });
  window.addEventListener("resize", function () {
    closeOpen(false);
  });
  window.addEventListener("pageshow", function () {
    Array.prototype.forEach.call(
      document.querySelectorAll("select[data-cdd-enhanced]"),
      function (s) {
        if (s._cdd) sync(s._cdd);
      }
    );
  });

  function start() {
    init(document);
    new MutationObserver(function (muts) {
      muts.forEach(function (m) {
        Array.prototype.forEach.call(m.addedNodes, function (n) {
          if (n.nodeType === 1 && !(n.closest && n.closest(".cdd-list"))) init(n);
        });
      });
    }).observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();

  window.CDD = {
    init: init,
    enhance: enhance,
    refresh: function (select) {
      if (select && select._cdd) build(select._cdd);
    },
    close: function () {
      closeOpen(false);
    },
  };
})();
