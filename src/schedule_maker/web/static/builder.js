/*
 * Перетаскивание пар в конструкторе.
 *
 * Браузер здесь ничего не решает: перед началом перетаскивания он спрашивает
 * у сервера, в какие ячейки пару вообще можно поставить, и подсвечивает их.
 * Окончательную проверку всё равно делает сервер — тем же движком правил,
 * которым пользуется генератор.
 */
(function () {
  "use strict";

  var GROUP = { name: "lessons", pull: true, put: true };
  var instances = [];
  var blockedReasons = {};

  function wrap() {
    return document.getElementById("sm-grid-wrap");
  }

  function csrfToken() {
    var input = document.querySelector('input[name="csrf_token"]');
    return input ? input.value : "";
  }

  function viewParams() {
    var node = wrap();
    return {
      kind: node ? node.dataset.kind : "group",
      subject_id: node ? node.dataset.subject : "",
      parity: node ? node.dataset.parity : "any",
    };
  }

  function clearHighlight() {
    document.querySelectorAll(".sm-cell").forEach(function (cell) {
      cell.classList.remove("sm-allowed", "sm-forbidden", "sm-over");
      cell.removeAttribute("data-reason");
    });
    blockedReasons = {};
  }

  /** Спросить у сервера маску допустимых ячеек и раскрасить сетку. */
  function highlight(item) {
    var params = new URLSearchParams({
      demand_id: item.dataset.demand,
      component: item.dataset.component || "0",
    });
    if (item.dataset.assignment) {
      params.set("assignment_id", item.dataset.assignment);
    }
    return fetch("/admin/builder/candidates?" + params.toString(), {
      headers: { "X-Requested-With": "fetch" },
    })
      .then(function (response) {
        return response.ok ? response.json() : { allowed: [], blocked: {} };
      })
      .then(function (data) {
        var allowed = {};
        data.allowed.forEach(function (pair) {
          allowed[pair[0] + "-" + pair[1]] = true;
        });
        blockedReasons = data.blocked || {};
        document.querySelectorAll(".sm-cell").forEach(function (cell) {
          var key = cell.dataset.day + "-" + cell.dataset.slot;
          if (allowed[key]) {
            cell.classList.add("sm-allowed");
          } else {
            cell.classList.add("sm-forbidden");
            if (blockedReasons[key]) {
              cell.setAttribute("title", blockedReasons[key]);
              cell.setAttribute("data-reason", blockedReasons[key]);
            }
          }
        });
      })
      .catch(function () {
        /* подсветка — вспомогательная вещь, без неё перетаскивание работает */
      });
  }

  function post(url, values) {
    var payload = Object.assign({ csrf_token: csrfToken() }, viewParams(), values);
    window.htmx.ajax("POST", url, {
      target: "#sm-grid-wrap",
      swap: "outerHTML",
      values: payload,
    });
  }

  function onEnd(evt) {
    clearHighlight();
    var item = evt.item;
    var target = evt.to;

    if (target.id === "sm-pool") {
      if (!item.dataset.assignment) {
        return; // из панели в панель — ничего не произошло
      }
      post("/admin/builder/unassign", { assignment_id: item.dataset.assignment });
      return;
    }

    var day = target.dataset.day;
    var slot = target.dataset.slot;
    if (day === undefined || slot === undefined) {
      return;
    }
    post("/admin/builder/move", {
      demand_id: item.dataset.demand,
      component: item.dataset.component || "0",
      day: day,
      slot: slot,
      assignment_id: item.dataset.assignment || "",
    });
  }

  function setup() {
    instances.forEach(function (instance) {
      instance.destroy();
    });
    instances = [];

    var options = {
      group: GROUP,
      animation: 120,
      draggable: ".sm-draggable",
      ghostClass: "sm-dragging",
      filter: "button, form",
      preventOnFilter: false,
      onStart: function (evt) {
        highlight(evt.item);
      },
      onEnd: onEnd,
    };

    document.querySelectorAll(".sm-cell-body").forEach(function (cell) {
      instances.push(new window.Sortable(cell, options));
    });
    var pool = document.getElementById("sm-pool");
    if (pool) {
      instances.push(new window.Sortable(pool, options));
    }
  }

  function init() {
    if (!window.Sortable || !window.htmx) {
      window.setTimeout(init, 50);
      return;
    }
    setup();
    // После обновления сетки сервером списки нужно подключить заново.
    document.body.addEventListener("htmx:afterSwap", function (evt) {
      if (evt.target && evt.target.id === "sm-grid-wrap") {
        setup();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
