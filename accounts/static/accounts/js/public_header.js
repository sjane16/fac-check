            (() => {
                const nav = document.getElementById("landing-nav");
                const toggle = document.getElementById("landing-menu-toggle");
                const menu = document.getElementById("landing-menu");
                const mobile = window.matchMedia("(max-width: 767px)");

                if (!nav || !toggle || !menu) return;

                function setOpen(open, restoreFocus = false) {
                    menu.classList.toggle('is-open', open);
                    toggle.setAttribute("aria-expanded", String(open));
                    toggle.setAttribute(
                        "aria-label",
                        open ? "Close navigation menu" : "Open navigation menu"
                    );

                    if (restoreFocus) toggle.focus();
                }
                toggle.addEventListener("click", () => {
                    setOpen(toggle.getAttribute("aria-expanded") !== "true");
                })

                // Close after choosing either the About, Login, or Register.
                menu.addEventListener("click", (event) => {
                    if (event.target.closest("a") && mobile.matches) {
                        setOpen(false, true);
                    }
                });

                // Close when clicking outside navigation.
                document.addEventListener("click", (event) => {
                    if (!nav.contains(event.target)) {
                        setOpen(false, menu.contains(document.activeElement));
                    }
                });

                // Escape closes the dropdown and returns focus to its button.
                document.addEventListener("keydown", (event) => {
                    if (
                        event.key === "Escape" &&
                        toggle.getAttribute("aria-expanded") === "true"
                    ) {
                        setOpen(false, true);
                    }
                });

                // Close when keyboard focus leaves the navigation.
                nav.addEventListener("focusout", (event) => {
                    if (!nav.contains(event.relatedTarget)) {
                        setOpen(false);
                    }
                });

                // Reset the dropdown when switching between desktop and mobile.
                mobile.addEventListener("change", () => {
                    setOpen(false, mobile.matches && menu.contains(document.activeElement));
                });

                nav.classList.add("menu-ready");
                toggle.hidden = false;
            })();
