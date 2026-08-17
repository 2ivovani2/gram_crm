export function initCommonUI(root = document) {
  root.querySelectorAll("[data-ui='faq']").forEach((item) => {
    const button = item.querySelector("button");
    if (!button) return;
    button.addEventListener("click", () => {
      const open = item.classList.toggle("is-open");
      button.setAttribute("aria-expanded", String(open));
    });
  });

  root.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (form.dataset.confirmed === "true") return;
      event.preventDefault();
      const dialog = document.querySelector("#g-confirm-dialog");
      if (!dialog?.showModal) {
        if (window.confirm(form.dataset.confirm)) form.submit();
        return;
      }
      dialog.querySelector("[data-confirm-copy]").textContent = form.dataset.confirm;
      dialog.showModal();
      dialog.querySelector("[data-confirm-submit]").onclick = () => {
        form.dataset.confirmed = "true";
        dialog.close();
        form.requestSubmit();
      };
    });
  });

  root.addEventListener("click", (event) => {
    const button = event.target.closest("button[value='delete'], button[data-confirm-action]");
    const form = button?.form;
    if (!button || !form || form.dataset.confirmed === "true") return;
    event.preventDefault();
    const dialog = document.querySelector("#g-confirm-dialog");
    const message = button.dataset.confirmAction || "Удалить запись? Это действие нельзя отменить.";
    if (!dialog?.showModal) {
      if (window.confirm(message)) form.requestSubmit(button);
      return;
    }
    dialog.querySelector("[data-confirm-copy]").textContent = message;
    dialog.showModal();
    dialog.querySelector("[data-confirm-submit]").onclick = () => {
      form.dataset.confirmed = "true";
      dialog.close();
      form.requestSubmit(button);
    };
  });

  root.querySelectorAll("form").forEach((form) => {
    form.addEventListener("submit", () => {
      const button = form.querySelector("button[type='submit']");
      if (!button || form.dataset.noLoading !== undefined) return;
      button.setAttribute("aria-busy", "true");
    });
  });
}
