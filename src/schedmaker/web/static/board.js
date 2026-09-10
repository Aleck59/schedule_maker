/*
 * Ручная правка расписания.
 *
 * При захвате карточки спрашиваем у сервера, куда её можно перенести без
 * нарушений, и подсвечиваем эти клетки зелёным. Перенос в любую другую клетку
 * тоже разрешён: диспетчер иногда обязан поставить пару неудобно, и его дело —
 * знать цену решения, а не спорить с программой. Поэтому после переноса
 * показываются нарушения, а не отказ.
 */
(function () {
  const board = document.getElementById("board");
  if (!board) return;
  const scheduleId = board.dataset.schedule;
  const status = document.getElementById("board-status");
  let dragged = null;

  function show(text, kind) {
    if (!status) return;
    status.textContent = "";
    status.className = "flash " + kind;
    status.style.display = "block";
    text.forEach(function (line, i) {
      if (i) status.appendChild(document.createElement("br"));
      status.appendChild(document.createTextNode(line));
    });
  }

  function clearHighlight() {
    board.querySelectorAll(".drop-cell").forEach(function (cell) {
      cell.classList.remove("allowed", "forbidden");
    });
  }

  board.addEventListener("dragstart", function (event) {
    const card = event.target.closest(".pair[draggable=true]");
    if (!card) return;
    dragged = card;
    card.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", card.dataset.placement);

    fetch("/admin/board/" + scheduleId + "/candidates?placement_id=" + card.dataset.placement)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        const allowed = new Set(
          data.allowed.map(function (s) { return s.day + ":" + s.period; })
        );
        board.querySelectorAll(".drop-cell").forEach(function (cell) {
          const key = cell.dataset.day + ":" + cell.dataset.period;
          cell.classList.add(allowed.has(key) ? "allowed" : "forbidden");
        });
      })
      .catch(function () { /* подсветка — подсказка, без неё правка всё равно работает */ });
  });

  board.addEventListener("dragend", function () {
    if (dragged) dragged.classList.remove("dragging");
    dragged = null;
    clearHighlight();
  });

  board.addEventListener("dragover", function (event) {
    if (event.target.closest(".drop-cell")) event.preventDefault();
  });

  board.addEventListener("drop", function (event) {
    const cell = event.target.closest(".drop-cell");
    if (!cell || !dragged) return;
    event.preventDefault();

    const body = new FormData();
    body.append("placement_id", dragged.dataset.placement);
    body.append("day", cell.dataset.day);
    body.append("period", cell.dataset.period);

    fetch("/admin/board/" + scheduleId + "/move", { method: "POST", body: body })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        cell.appendChild(dragged);
        clearHighlight();
        if (data.ok && !data.violations.length) {
          show(["Перенесено: " + data.moved_to + ". Нарушений нет."], "ok");
          return;
        }
        const lines = ["Перенесено: " + data.moved_to + ", но есть замечания:"];
        data.violations.forEach(function (v) {
          lines.push((v.hard ? "Нарушение: " : "Неудобство: ") + v.message +
                     (v.hint ? " " + v.hint : ""));
        });
        show(lines, data.ok ? "warn" : "err");
      })
      .catch(function () { show(["Не удалось сохранить перенос."], "err"); });
  });

  board.addEventListener("click", function (event) {
    const button = event.target.closest(".pin-btn");
    if (!button) return;
    const body = new FormData();
    body.append("placement_id", button.dataset.placement);
    fetch("/admin/board/" + scheduleId + "/pin", { method: "POST", body: body })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        button.textContent = data.pinned ? "открепить" : "закрепить";
        button.closest(".pair").classList.toggle("pinned", data.pinned);
        show([data.pinned
          ? "Пара закреплена — перегенерация её не сдвинет."
          : "Пара откреплена."], "ok");
      });
  });
})();
