(() => {
    const form = document.getElementById('contact-form');
    if (!form) return;
    const feedback = document.getElementById('contact-feedback');
    const fields = [...form.querySelectorAll('#contact-name, #contact-email, #contact-phone, #contact-message')];
    const messages = {
        'contact-name': 'Please enter your full name.',
        'contact-email': 'Please enter a valid email address.',
        'contact-message': 'Please enter your message.',
    };

    function validate(field) {
        let message = '';
        if (field.required && !field.value.trim()) message = messages[field.id];
        else if (!field.validity.valid) message = messages[field.id] || 'Please check this field.';
        const error = document.getElementById(`${field.id}-error`);
        error.textContent = message;
        error.hidden = !message;
        field.setAttribute('aria-invalid', String(Boolean(message)));
        return !message;
    }

    fields.forEach(field => {
        const error = document.createElement('p');
        error.id = `${field.id}-error`;
        error.className = 'field-error';
        error.hidden = true;
        field.after(error);
        field.setAttribute('aria-describedby', error.id);
        field.addEventListener('input', () => {
            feedback.hidden = true;
            if (field.getAttribute('aria-invalid') === 'true') validate(field);
        });
    });

    form.addEventListener('submit', async event => {
        event.preventDefault();
        const invalid = fields.filter(field => !validate(field));
        if (invalid.length) {
            feedback.hidden = true;
            invalid[0].focus();
            return;
        }
        const button = form.querySelector('button[type="submit"]');
        button.disabled = true;
        feedback.textContent = 'Sending your message…';
        feedback.hidden = false;
        try {
            const response = await fetch(form.action, {
                method: 'POST',
                body: new FormData(form),
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
            });
            const result = await response.json();
            feedback.textContent = result.message;
            if (result.errors) {
                Object.entries(result.errors).forEach(([name, message]) => {
                    const field = form.elements.namedItem(name);
                    if (!field || !field.id) return;
                    const error = document.getElementById(`${field.id}-error`);
                    error.textContent = message;
                    error.hidden = false;
                    field.setAttribute('aria-invalid', 'true');
                });
            }
            if (result.ok) form.reset();
        } catch (_) {
            feedback.textContent = 'Your message could not be sent. Please try again later.';
        } finally {
            button.disabled = false;
            feedback.focus();
        }
    });
})();
