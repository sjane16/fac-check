(function () {
    "use strict";
    const panel = document.getElementById("personnelPanel");
    if (!panel) return;
    const toggle = document.getElementById("managementListSwitch");
    const rows = document.getElementById("personnelManagementRows");
    const count = document.getElementById("personnelCount");
    const loadingRows = document.getElementById("personnelLoadingTemplate").innerHTML;
    const search = document.getElementById("personnelSearch");
    const userSearch = document.getElementById("userSearch");
    const mobileSearchButton = document.getElementById("managementMobileSearchButton");
    const mobileSearchField = document.getElementById("managementMobileSearchField");
    const mobileSearchInput = document.getElementById("managementMobileSearchInput");
    const controls = document.getElementById("managementControls");
    const categoryFilter = document.getElementById("personnelCategoryFilter");
    const prev = document.getElementById("personnelPrev");
    const next = document.getElementById("personnelNext");
    const dialog = document.getElementById("personnelManagementDialog");
    const form = document.getElementById("personnelManagementForm");
    const name = document.getElementById("personnelManagementName");
    const category = document.getElementById("personnelManagementCategory");
    const submit = document.getElementById("personnelManagementSubmit");
    const cancel = document.getElementById("personnelManagementCancel");
    const error = document.getElementById("personnelManagementError");
    let mode = "add", targetId = null, page = 1, busy = false, searchTimer, loadSequence = 0;
    let mobileSearchTimer;

    function mobileSearchSource() {
        return panel.hidden ? userSearch : search;
    }

    function syncMobileSearch() {
        mobileSearchInput.value = mobileSearchSource().value;
        mobileSearchInput.placeholder = panel.hidden ? "Search name or email" : "Search personnel name";
        const label = panel.hidden ? "Search users" : "Search personnels";
        mobileSearchInput.setAttribute("aria-label", label);
        mobileSearchButton.setAttribute("aria-label", label);
    }

    function setMobileSearchOpen(open, focus = true) {
        controls.dataset.mobileSearchOpen = String(open);
        mobileSearchField.hidden = !open;
        mobileSearchButton.setAttribute("aria-expanded", String(open));
        if (window.CDD) CDD.close();
        if (open) syncMobileSearch();
        if (focus) (open ? mobileSearchInput : mobileSearchButton).focus();
    }

    function applyMobileSearch() {
        const source = mobileSearchSource();
        source.value = mobileSearchInput.value;
        source.dispatchEvent(new Event("search", { bubbles: true }));
    }

    function closeMobileSearch() {
        clearTimeout(mobileSearchTimer);
        mobileSearchInput.value = "";
        applyMobileSearch();
        setMobileSearchOpen(false);
    }

    mobileSearchButton.addEventListener("click", () => setMobileSearchOpen(true));
    document.getElementById("managementMobileSearchClose").addEventListener("click", closeMobileSearch);
    mobileSearchInput.addEventListener("input", () => {
        clearTimeout(mobileSearchTimer);
        mobileSearchTimer = setTimeout(applyMobileSearch, 250);
    });
    mobileSearchInput.addEventListener("keydown", event => {
        if (event.key === "Escape") { event.preventDefault(); closeMobileSearch(); }
    });
    window.matchMedia("(min-width: 1024px)").addEventListener("change", event => {
        if (event.matches) {
            clearTimeout(mobileSearchTimer);
            if (controls.dataset.mobileSearchOpen === "true") applyMobileSearch();
            setMobileSearchOpen(false, false);
        }
    });

    async function load() {
        const sequence = ++loadSequence;
        rows.innerHTML = loadingRows;
        rows.setAttribute("aria-busy", "true");
        count.innerHTML = '<span class="skeleton inline-block h-3 w-24 rounded align-middle" aria-hidden="true"></span>';
        document.getElementById("personnelPageInfo").textContent = "";
        prev.disabled = next.disabled = true;
        const params = new URLSearchParams({ search: search.value.trim(), category: categoryFilter.value, page });
        try {
            const response = await fetch(panel.dataset.listUrl + "?" + params);
            if (!response.ok || response.redirected) throw new Error();
            const data = await response.json();
            if (sequence !== loadSequence) return;
            rows.innerHTML = data.html;
            count.textContent = data.total_count + " personnel found";
            document.getElementById("personnelPageInfo").textContent = "Page " + data.page + " of " + data.total_pages;
            page = data.page;
            prev.disabled = !data.has_previous;
            next.disabled = !data.has_next;
        } catch (_) {
            if (sequence !== loadSequence) return;
            rows.textContent = "Couldn't load personnel. Switch back and try again.";
            count.textContent = "Unable to load personnel";
        } finally {
            if (sequence === loadSequence) rows.removeAttribute("aria-busy");
        }
    }

    toggle.addEventListener("click", function () {
        const showPersonnel = panel.hidden;
        panel.hidden = !showPersonnel;
        document.getElementById("personnelFilters").hidden = !showPersonnel;
        count.hidden = !showPersonnel;
        ["userList", "userFilters", "userCount"].forEach(id => { document.getElementById(id).hidden = showPersonnel; });
        document.getElementById("managementListTitle").textContent = showPersonnel ? "All Personnels" : "All Users";
        const label = showPersonnel ? "Switch to All Users" : "Switch to All Personnels";
        toggle.setAttribute("aria-label", label);
        toggle.title = label;
        toggle.setAttribute("aria-pressed", String(showPersonnel));
        clearTimeout(mobileSearchTimer);
        syncMobileSearch();
        if (showPersonnel) load();
        else document.dispatchEvent(new Event("management:refresh-users"));
    });
    document.querySelectorAll("[data-stat-filter]").forEach(card => {
        card.addEventListener("click", () => { if (!panel.hidden) toggle.click(); }, true);
    });
    search.addEventListener("input", function () {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => { page = 1; load(); }, 250);
    });
    search.addEventListener("search", () => { clearTimeout(searchTimer); page = 1; load(); });
    categoryFilter.addEventListener("change", () => { page = 1; load(); });
    prev.addEventListener("click", () => { page -= 1; load(); });
    next.addEventListener("click", () => { page += 1; load(); });

    function open(action, button) {
        mode = action;
        targetId = button ? button.dataset.id : null;
        const deleting = mode === "delete";
        document.getElementById("personnelManagementTitle").textContent = deleting ? "Delete " + button.dataset.name + "?" : (mode === "edit" ? "Edit Personnel" : "Add Personnel");
        document.getElementById("personnelManagementFields").hidden = deleting;
        document.getElementById("personnelDeleteExplanation").hidden = !deleting;
        name.disabled = category.disabled = deleting;
        name.value = button ? button.dataset.name : "";
        category.value = button && button.dataset.category ? button.dataset.category : category.options[0].value;
        error.hidden = true;
        submit.textContent = deleting ? "Delete Personnel" : "Save Personnel";
        submit.classList.toggle("bg-red-600", deleting);
        submit.classList.toggle("bg-blue-600", !deleting);
        submit.classList.toggle("hover:bg-red-700", deleting);
        submit.classList.toggle("hover:bg-blue-700", !deleting);
        dialog.showModal();
        (deleting ? cancel : name).focus();
    }
    document.getElementById("openAddPersonnel").addEventListener("click", () => open("add"));
    rows.addEventListener("click", event => {
        const button = event.target.closest("[data-personnel-action]");
        if (button) open(button.dataset.personnelAction, button);
    });
    cancel.addEventListener("click", () => { if (!busy) dialog.close(); });
    dialog.addEventListener("cancel", event => { if (busy) event.preventDefault(); });
    form.addEventListener("submit", async function (event) {
        event.preventDefault();
        if (busy) return;
        busy = true;
        const payload = new FormData(form);
        [submit, cancel, name, category].forEach(control => { control.disabled = true; });
        error.hidden = true;
        const url = mode === "add" ? panel.dataset.addUrl : panel.dataset[mode + "Url"].replace("/0/", "/" + targetId + "/");
        try {
            const response = await fetch(url, { method: "POST", credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" }, body: payload });
            if (response.redirected) throw new Error("Your session changed. Refresh the page and try again.");
            const data = await response.json();
            if (!response.ok || !data.ok) throw new Error(data.error || "Couldn't save personnel.");
            dialog.close();
            await load();
            toggle.focus();
        } catch (exception) {
            error.textContent = exception.message || "Couldn't reach the server. Please try again.";
            error.hidden = false;
        } finally {
            busy = false;
            submit.disabled = cancel.disabled = false;
            name.disabled = category.disabled = mode === "delete";
        }
    });
})();
