(() => {
  const token = location.pathname.split("/")[1];
  const byId = (id) => document.getElementById(id);
  const elements = {
    statusBanner: byId("statusBanner"),
    statusText: byId("statusText"),
    diskList: byId("diskList"),
    diskCount: byId("diskCount"),
    diskError: byId("diskError"),
    selectedImage: byId("selectedImage"),
    imageName: byId("imageName"),
    imageMeta: byId("imageMeta"),
    imageState: byId("imageState"),
    imageFile: byId("imageFile"),
    imagePath: byId("imagePath"),
    uploadProgress: byId("uploadProgress"),
    uploadName: byId("uploadName"),
    uploadPercent: byId("uploadPercent"),
    uploadProgressBar: byId("uploadProgressBar"),
    writeButton: byId("writeButton"),
    unmountButton: byId("unmountButton"),
    activityCard: byId("activityCard"),
    activityText: byId("activityText"),
    activityProgress: byId("activityProgress"),
    logSection: byId("logSection"),
    logToggle: byId("logToggle"),
    logPanel: byId("logPanel"),
    logOutput: byId("logOutput"),
    logCount: byId("logCount"),
    woeusbStatus: byId("woeusbStatus"),
    woeusbStatusText: byId("woeusbStatusText"),
    actionDialog: byId("actionDialog"),
    actionForm: byId("actionForm"),
    confirmRow: byId("confirmRow"),
    confirmCheck: byId("confirmCheck"),
    passwordRow: byId("passwordRow"),
    sudoPassword: byId("sudoPassword"),
    passwordHint: byId("passwordHint"),
    dialogDevice: byId("dialogDevice"),
    dialogTitle: byId("dialogTitle"),
    dialogDescription: byId("dialogDescription"),
    dialogSubmit: byId("dialogSubmit"),
    toast: byId("toast"),
  };

  let state = null;
  let statusRequestInFlight = false;
  let lastImageKey = null;
  let lastDiskKey = null;
  let selectedDisk = null;
  let lastLogId = 0;
  let lastResultKey = "";
  let toastTimer = null;
  let logCollapseTimer = null;
  let pendingAction = null;
  let currentRequest = null;

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      headers: {
        "X-Session-Token": token,
        ...(options.headers || {}),
      },
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || `Ошибка сервера (${response.status})`);
    return body;
  }

  function showToast(message, isError = false) {
    elements.toast.textContent = message;
    elements.toast.classList.toggle("error", isError);
    elements.toast.classList.add("visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => elements.toast.classList.remove("visible"), 4200);
  }

  function setStatus(message, isError = false) {
    elements.statusText.textContent = message;
    elements.statusBanner.classList.toggle("error", isError);
  }

  function canWrite() {
    if (!state?.image || !selectedDisk || selectedDisk.read_only || selectedDisk.mounted) {
      return false;
    }
    const mode = document.querySelector('input[name="mode"]:checked')?.value;
    if (mode === "windows") {
      return (
        state.image.name.toLowerCase().endsWith(".iso") &&
        Boolean(state.woeusb?.path)
      );
    }
    return true;
  }

  function addLogs(entries) {
    if (!entries?.length) return;
    const placeholder = elements.logOutput.querySelector(".log-placeholder");
    if (placeholder) placeholder.remove();
    for (const entry of entries) {
      const line = document.createElement("p");
      line.className = "log-line";
      if (entry.text.startsWith("$ ")) line.classList.add("command");
      if (/ошибка|error|failed/i.test(entry.text)) line.classList.add("error");
      const time = document.createElement("span");
      time.className = "log-time";
      time.textContent = entry.time;
      line.append(time, document.createTextNode(entry.text));
      elements.logOutput.append(line);
      lastLogId = Math.max(lastLogId, entry.id);
    }
    while (elements.logOutput.children.length > 3000) {
      elements.logOutput.firstElementChild.remove();
    }
    elements.logCount.textContent = String(elements.logOutput.children.length);
    elements.logOutput.scrollTop = elements.logOutput.scrollHeight;
  }

  function renderImage(image) {
    if (!image) {
      elements.imagePath.value = "";
      elements.selectedImage.classList.add("empty");
      elements.imageName.textContent = "Образ не выбран";
      elements.imageMeta.textContent = "Выбери файл или укажи путь к нему";
      elements.imageState.textContent = "ОЖИДАНИЕ";
      return;
    }
    elements.selectedImage.classList.remove("empty");
    elements.imageName.textContent = image.name;
    elements.imageMeta.textContent =
      `${image.size_label} · ${image.uploaded ? "загружен в приложение" : image.path}`;
    elements.imageState.textContent = "ГОТОВ";
    elements.imagePath.value = image.uploaded ? "" : image.path;
  }

  function renderDisks(disks) {
    if (selectedDisk && !disks.some((disk) => disk.path === selectedDisk.path)) {
      selectedDisk = null;
    }
    if (selectedDisk) {
      selectedDisk = disks.find((disk) => disk.path === selectedDisk.path);
    }
    const diskKey = JSON.stringify(disks);
    elements.diskCount.textContent = disks.length
      ? `${disks.length} ${disks.length === 1 ? "НАКОПИТЕЛЬ" : "НАКОПИТЕЛЕЙ"}`
      : "НАКОПИТИТЕЛИ НЕ НАЙДЕНЫ";
    if (diskKey === lastDiskKey) {
      for (const card of elements.diskList.querySelectorAll(".disk-card")) {
        const selected = card.dataset.path === selectedDisk?.path;
        card.classList.toggle("selected", selected);
        card.setAttribute("aria-pressed", String(selected));
      }
      return;
    }
    lastDiskKey = diskKey;
    if (!disks.length) {
      elements.diskList.innerHTML =
        '<div class="empty-disks"><span>USB-накопители не найдены. ' +
        "Подключи флешку и обнови список.</span></div>";
      return;
    }
    elements.diskList.replaceChildren();
    for (const disk of disks) {
      const card = document.createElement("button");
      card.type = "button";
      card.className =
        `disk-card${selectedDisk?.path === disk.path ? " selected" : ""}`;
      card.dataset.path = disk.path;
      card.setAttribute(
        "aria-pressed",
        String(selectedDisk?.path === disk.path),
      );
      const symbol = document.createElement("span");
      symbol.className = "disk-symbol";
      symbol.innerHTML =
        '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="3" width="16" height="18" rx="3"/><path d="M8 7h8m-8 4h8m-8 4h4m4 0h.01"/></svg>';
      const info = document.createElement("span");
      info.className = "disk-info";
      const title = document.createElement("strong");
      title.textContent = `${disk.path} — ${disk.model}`;
      const details = document.createElement("small");
      details.textContent = disk.size_label;
      info.append(title, details);
      const tags = document.createElement("span");
      tags.className = "disk-tags";
      if (disk.read_only) tags.append(makeTag("ТОЛЬКО ЧТЕНИЕ", "read-only"));
      if (disk.mounted) tags.append(makeTag("СМОНТИРОВАН", "mounted"));
      const radio = document.createElement("span");
      radio.className = "disk-radio";
      radio.setAttribute("aria-hidden", "true");
      card.append(symbol, info, tags, radio);
      card.addEventListener("click", () => {
        selectedDisk = state.disks.find((item) => item.path === disk.path) || null;
        renderDisks(state.disks);
        updateButtons();
      });
      elements.diskList.append(card);
    }
  }

  function makeTag(text, className) {
    const tag = document.createElement("span");
    tag.className = `disk-tag ${className}`;
    tag.textContent = text;
    return tag;
  }

  function renderWoeusb(woeusb) {
    if (woeusb?.path && woeusb.check?.ok) {
      elements.woeusbStatus.className = "woeusb-status ready";
      elements.woeusbStatusText.textContent = "WoeUSB-ng установлен и прошёл проверку";
    } else if (woeusb?.path) {
      elements.woeusbStatus.className = "woeusb-status";
      elements.woeusbStatusText.textContent = "WoeUSB-ng найден — проверь перед записью";
    } else {
      elements.woeusbStatus.className = "woeusb-status error";
      elements.woeusbStatusText.textContent = "WoeUSB-ng не установлен";
    }
  }

  function updateButtons() {
    const busy = Boolean(state?.busy);
    elements.imageFile.disabled = busy;
    elements.imagePath.disabled = busy;
    byId("pathForm").querySelector("button").disabled = busy;
    elements.writeButton.disabled = busy || !canWrite();
    elements.unmountButton.disabled =
      busy || !selectedDisk || !selectedDisk.mounted;
    byId("installWoeusbButton").disabled = busy;
    byId("checkWoeusbButton").disabled = busy;
    byId("refreshButton").disabled = busy;
    document.querySelectorAll('input[name="mode"]').forEach((input) => {
      input.disabled = busy;
    });
  }

  function renderState(nextState) {
    const wasBusy = Boolean(state?.busy);
    state = nextState;
    addLogs(nextState.logs);
    elements.diskError.hidden = !nextState.disk_error;
    elements.diskError.textContent = nextState.disk_error || "";
    renderDisks(nextState.disks || []);
    const imageKey = JSON.stringify(nextState.image);
    if (imageKey !== lastImageKey) {
      lastImageKey = imageKey;
      renderImage(nextState.image);
    }
    renderWoeusb(nextState.woeusb);

    if (nextState.busy) {
      clearTimeout(logCollapseTimer);
      setStatus(nextState.status || "Выполняется операция");
      elements.activityText.textContent = nextState.status || "Выполняется операция";
      elements.activityCard.hidden = false;
      if (!elements.logSection.classList.contains("open")) toggleLog(true);
    } else {
      elements.activityCard.hidden = true;
      setStatus(nextState.status || "Готово к работе", Boolean(nextState.disk_error));
      if (wasBusy) {
        clearTimeout(logCollapseTimer);
        logCollapseTimer = setTimeout(() => toggleLog(false), 3000);
      }
      if (nextState.result) {
        const key = `${nextState.operation}:${nextState.result}:${nextState.last_log_id}`;
        if (key !== lastResultKey) {
          lastResultKey = key;
          showToast(
            nextState.result === "success" ? nextState.status : lastErrorMessage(),
            nextState.result !== "success",
          );
          if (!wasBusy) {
            clearTimeout(logCollapseTimer);
            logCollapseTimer = setTimeout(() => toggleLog(false), 3000);
          }
        }
      }
    }
    updateButtons();
  }

  function lastErrorMessage() {
    const lines = elements.logOutput.querySelectorAll(".log-line");
    for (let index = lines.length - 1; index >= 0; index -= 1) {
      if (lines[index].classList.contains("error")) {
        return lines[index].textContent.replace(/^\d\d:\d\d:\d\d/, "").trim();
      }
    }
    return "Операция завершилась с ошибкой. Открой журнал для подробностей.";
  }

  async function refreshState() {
    if (statusRequestInFlight) return;
    statusRequestInFlight = true;
    try {
      const nextState = await api(`/api/status?after=${lastLogId}`);
      renderState(nextState);
    } catch (error) {
      setStatus(error.message, true);
    } finally {
      statusRequestInFlight = false;
    }
  }

  function toggleLog(force) {
    const open = typeof force === "boolean"
      ? force
      : !elements.logSection.classList.contains("open");
    elements.logSection.classList.toggle("open", open);
    elements.logToggle.setAttribute("aria-expanded", String(open));
    elements.logPanel.setAttribute("aria-hidden", String(!open));
  }

  function openActionDialog({ operation, title, description, device, needsPassword = true }) {
    pendingAction = operation;
    elements.dialogTitle.textContent = title;
    elements.dialogDescription.textContent = description;
    elements.dialogDevice.hidden = !device;
    elements.dialogDevice.textContent = device || "";
    elements.confirmRow.hidden = !needsPassword;
    elements.confirmCheck.checked = false;
    elements.confirmCheck.required = needsPassword;
    elements.passwordRow.hidden = !needsPassword;
    elements.sudoPassword.hidden = !needsPassword;
    elements.sudoPassword.required = needsPassword;
    elements.passwordHint.hidden = !needsPassword;
    elements.dialogSubmit.disabled = needsPassword;
    elements.dialogSubmit.textContent = needsPassword ? "Подтвердить →" : "Проверить →";
    elements.sudoPassword.value = "";
    elements.actionDialog.showModal();
    if (needsPassword) elements.sudoPassword.focus();
  }

  async function startAction(operation, extra = {}) {
    try {
      await api("/api/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ operation, ...extra }),
      });
      elements.actionDialog.close();
      await refreshState();
    } catch (error) {
      showToast(error.message, true);
    }
  }

  async function setImagePath(path) {
    if (!path.trim()) {
      showToast("Укажи полный путь к образу.", true);
      return;
    }
    try {
      const response = await api("/api/image/path", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
      });
      state.image = response.image;
      lastImageKey = JSON.stringify(response.image);
      renderImage(response.image);
      showToast("Образ выбран");
      updateButtons();
    } catch (error) {
      showToast(error.message, true);
    }
  }

  function uploadImage(file) {
    if (!file) return;
    const extension = file.name.split(".").pop().toLowerCase();
    if (!["iso", "img", "bin", "raw", "dsk"].includes(extension)) {
      showToast("Выбери файл ISO, IMG, BIN, RAW или DSK.", true);
      return;
    }
    if (currentRequest) currentRequest.abort();
    elements.uploadProgress.hidden = false;
    elements.uploadName.textContent = `Загрузка ${file.name}`;
    elements.uploadPercent.textContent = "0%";
    elements.uploadProgressBar.style.width = "0%";
    const request = new XMLHttpRequest();
    currentRequest = request;
    request.open("POST", "/api/image/upload");
    request.setRequestHeader("X-Session-Token", token);
    request.setRequestHeader("X-Image-Name", encodeURIComponent(file.name));
    request.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      const percent = Math.round((event.loaded / event.total) * 100);
      elements.uploadPercent.textContent = `${percent}%`;
      elements.uploadProgressBar.style.width = `${percent}%`;
    };
    request.onload = () => {
      currentRequest = null;
      const response = JSON.parse(request.responseText || "{}");
      if (response.error) {
        showToast(response.error, true);
        elements.uploadProgress.hidden = true;
        return;
      }
      if (state) state.image = response.image;
      lastImageKey = JSON.stringify(response.image);
      renderImage(response.image);
      elements.uploadPercent.textContent = "100%";
      showToast("Образ загружен");
      updateButtons();
      setTimeout(() => { elements.uploadProgress.hidden = true; }, 1200);
    };
    request.onerror = () => {
      currentRequest = null;
      elements.uploadProgress.hidden = true;
      showToast("Не удалось загрузить файл.", true);
    };
    request.onabort = () => {
      currentRequest = null;
      elements.uploadProgress.hidden = true;
    };
    request.send(file);
  }

  byId("refreshButton").addEventListener("click", refreshState);
  byId("imageFile").addEventListener("change", (event) => {
    uploadImage(event.target.files[0]);
    event.target.value = "";
  });
  byId("pathForm").addEventListener("submit", (event) => {
    event.preventDefault();
    setImagePath(elements.imagePath.value);
  });
  document.querySelectorAll('input[name="mode"]').forEach((input) => {
    input.addEventListener("change", () => {
      byId("rawModeCard").classList.toggle("selected", input.value === "raw" && input.checked);
      byId("windowsModeCard").classList.toggle("selected", input.value === "windows" && input.checked);
      updateButtons();
    });
  });
  byId("writeButton").addEventListener("click", () => {
    if (!state?.image || !selectedDisk) return;
    const windows = document.querySelector('input[name="mode"]:checked').value === "windows";
    openActionDialog({
      operation: "write",
      title: "Начать запись?",
      description: windows
        ? "WoeUSB-ng создаст установочную флешку Windows и удалит все данные на ней."
        : "Образ будет записан на весь USB-накопитель. Все существующие данные будут удалены.",
      device: `${selectedDisk.path} · ${selectedDisk.model} · ${selectedDisk.size_label}`,
    });
  });
  byId("unmountButton").addEventListener("click", () => {
    if (!selectedDisk) return;
    openActionDialog({
      operation: "unmount",
      title: "Размонтировать накопитель?",
      description: "Это безопасно отключит файловые системы USB. Данные не удаляются.",
      device: `${selectedDisk.path} · ${selectedDisk.model}`,
    });
  });
  byId("installWoeusbButton").addEventListener("click", () => {
    openActionDialog({
      operation: "install",
      title: "Установить WoeUSB-ng?",
      description:
        "Будут установлены системные зависимости через пакетный менеджер дистрибутива и приложение " +
        "WoeUSB-ng в пользовательское окружение. Потребуется интернет.",
    });
  });
  byId("checkWoeusbButton").addEventListener("click", () => {
    startAction("check");
  });
  byId("actionForm").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!pendingAction) return;
    const operation = pendingAction;
    pendingAction = null;
    const password = elements.sudoPassword.value;
    elements.sudoPassword.value = "";
    const extra = operation === "write"
      ? {
          confirmed: true,
          password,
          device: selectedDisk?.path,
          mode: document.querySelector('input[name="mode"]:checked').value,
        }
      : operation === "unmount"
        ? { confirmed: true, password, device: selectedDisk?.path }
        : { confirmed: true, password };
    startAction(operation, extra);
  });
  byId("confirmCheck").addEventListener("change", () => {
    elements.dialogSubmit.disabled =
      elements.confirmCheck.required && !elements.confirmCheck.checked;
  });
  byId("dialogClose").addEventListener("click", () => elements.actionDialog.close());
  byId("dialogCancel").addEventListener("click", () => elements.actionDialog.close());
  byId("logToggle").addEventListener("click", () => toggleLog());
  byId("clearLogButton").addEventListener("click", () => {
    elements.logOutput.replaceChildren();
    elements.logCount.textContent = "0";
    if (state) lastLogId = state.last_log_id;
  });

  const uploadCard = document.querySelector(".upload-card");
  for (const eventName of ["dragenter", "dragover"]) {
    uploadCard.addEventListener(eventName, (event) => {
      event.preventDefault();
      uploadCard.classList.add("drag-over");
    });
  }
  for (const eventName of ["dragleave", "drop"]) {
    uploadCard.addEventListener(eventName, (event) => {
      event.preventDefault();
      uploadCard.classList.remove("drag-over");
    });
  }
  uploadCard.addEventListener("drop", (event) => {
    uploadImage(event.dataTransfer.files[0]);
  });

  refreshState();
  setInterval(refreshState, 1600);
})();
