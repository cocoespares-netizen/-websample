const menuToggle = document.querySelector(".menu-toggle");
const mainNav = document.querySelector(".main-nav");
const contactLink = mainNav?.querySelector('a[href$="#contact"]');
const routeLink = mainNav?.querySelector('a[aria-current="page"]');

if (contactLink && routeLink) {
  const updateCurrentNav = () => {
    const activeLink = window.location.hash === "#contact" ? contactLink : routeLink;
    mainNav.querySelectorAll("a").forEach((link) => {
      const active = link === activeLink;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
  };

  updateCurrentNav();
  window.addEventListener("hashchange", updateCurrentNav);
}

document.querySelectorAll(".flash").forEach((message) => {
  const dismissDelay = message.classList.contains("flash-logout") ? 1600 : 2500;
  window.setTimeout(() => {
    message.classList.add("is-dismissing");
    window.setTimeout(() => message.remove(), 180);
  }, dismissDelay);
});

if (menuToggle && mainNav) {
  menuToggle.addEventListener("click", () => {
    const expanded = menuToggle.getAttribute("aria-expanded") === "true";
    menuToggle.setAttribute("aria-expanded", String(!expanded));
    mainNav.classList.toggle("is-open", !expanded);
  });
}

document.querySelectorAll(".password-toggle").forEach((toggle) => {
  toggle.addEventListener("click", () => {
    const input = document.getElementById(toggle.getAttribute("aria-controls"));
    if (!(input instanceof HTMLInputElement)) return;
    const reveal = input.type === "password";
    input.type = reveal ? "text" : "password";
    toggle.setAttribute("aria-pressed", String(reveal));
    toggle.setAttribute("aria-label", reveal ? "Hide password" : "Show password");
  });
});

document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});

const sortSelect = document.querySelector("#sort-products");

if (sortSelect) {
  sortSelect.addEventListener("change", () => sortSelect.form?.requestSubmit());
}

const productImageInput = document.querySelector("#product-image-input");
const productImagePreview = document.querySelector("#product-image-preview");
const productImagePlaceholder = document.querySelector("#product-image-placeholder");
const productImageFilename = document.querySelector("#product-image-filename");

if (productImageInput && productImagePreview && productImagePlaceholder && productImageFilename) {
  let previewUrl = null;
  productImageInput.addEventListener("change", () => {
    const file = productImageInput.files?.[0];
    if (!file) return;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(file);
    productImagePreview.src = previewUrl;
    productImagePreview.hidden = false;
    productImagePlaceholder.hidden = true;
    productImageFilename.textContent = file.name;
  });
}

const productGrid = document.querySelector("#product-grid");
const viewButtons = document.querySelectorAll(".view-toggle-button");

if (productGrid && viewButtons.length) {
  viewButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const isList = button.dataset.view === "list";
      productGrid.classList.toggle("is-list", isList);
      viewButtons.forEach((viewButton) => {
        const active = viewButton === button;
        viewButton.classList.toggle("is-active", active);
        viewButton.setAttribute("aria-pressed", String(active));
      });
    });
  });
}

document.querySelectorAll(".favorite-toggle").forEach((button) => {
  button.addEventListener("click", () => {
    const favorite = button.getAttribute("aria-pressed") !== "true";
    button.setAttribute("aria-pressed", String(favorite));
    button.setAttribute(
      "aria-label",
      favorite
        ? "Remove from favorites"
        : button.getAttribute("aria-label").replace("Remove from favorites", "Add to favorites"),
    );
    button.textContent = favorite ? "♥" : "♡";
  });
});

const mainProductImage = document.querySelector("#main-product-image");

if (mainProductImage) {
  document.querySelectorAll(".product-thumbnail").forEach((thumbnail) => {
    thumbnail.addEventListener("click", () => {
      mainProductImage.src = thumbnail.dataset.image;
      document.querySelectorAll(".product-thumbnail").forEach((item) => {
        const active = item === thumbnail;
        item.classList.toggle("is-active", active);
        item.setAttribute("aria-pressed", String(active));
      });
    });
  });
}

document.querySelectorAll("[data-quantity-step]").forEach((button) => {
  button.addEventListener("click", () => {
    const input = button.closest(".quantity-control")?.querySelector("input");
    if (!(input instanceof HTMLInputElement)) return;
    const min = Number(input.min) || 1;
    const max = Number(input.max) || 10;
    const step = Number(button.dataset.quantityStep);
    input.value = String(Math.min(max, Math.max(min, Number(input.value) + step)));
  });
});

document.querySelectorAll('.size-picker input[name="size"]').forEach((input) => {
  input.addEventListener("change", () => {
    const label = input.closest(".size-picker")?.querySelector(".size-selection");
    if (label) label.textContent = `— ${input.value}`;
  });
});

document.querySelectorAll(".detail-wishlist").forEach((button) => {
  button.addEventListener("click", () => {
    const saved = button.getAttribute("aria-pressed") !== "true";
    button.setAttribute("aria-pressed", String(saved));
    button.innerHTML = saved ? "♥ &nbsp; Added to Wishlist" : "♡ &nbsp; Add to Wishlist";
  });
});
