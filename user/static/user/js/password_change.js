// Show checklist on focus, hide it again once the field loses focus
function initPasswordChecklistToggle() {
  const newPasswordField = document.getElementById("new_password1");
  const requirementsList = document.getElementById("password-requirements");
  if (!newPasswordField || !requirementsList) return;

  newPasswordField.addEventListener("focus", function () {
    requirementsList.classList.remove("hidden");
  });
  newPasswordField.addEventListener("blur", function () {
    // Only hide when the field is empty.
    if (newPasswordField.value.trim() === "") {
      requirementsList.classList.add("hidden");
    }
  });
}

// Toggles a password field between hidden dots and plain text (eye icon click)
function togglePasswordVisibility(fieldId) {
  const input = document.getElementById(fieldId);
  const slashEye = document.getElementById(fieldId + "_slash");
  const openEye = document.getElementById(fieldId + "_open");

  if (!slashEye.classList.contains("hidden")) {
    input.type = "text";
    slashEye.classList.add("hidden");
    openEye.classList.remove("hidden");
  } else {
    input.type = "password";
    openEye.classList.add("hidden");
    slashEye.classList.remove("hidden");
  }
}

// Enables the "Update Password" button as soon as the user types in any password field (starts disabled)
function initPasswordChangeButton() {
  const passwordForm = document.getElementById("password_form");
  const changePasswordBtn = document.getElementById("change_password_btn");
  if (!passwordForm || !changePasswordBtn) return;

  function enableChangePasswordBtn() {
    changePasswordBtn.disabled = false;
    changePasswordBtn.classList.remove("opacity-50", "cursor-not-allowed");
  }

  passwordForm.querySelectorAll("input").forEach((field) => {
    field.addEventListener("input", enableChangePasswordBtn);
  });
}

// Flips one checklist row between "unmet" (gray circle) and "met" (green check)
function setRequirementMet(key, met) {
  document.getElementById(`req-${key}-unmet`).classList.toggle("hidden", met);
  document.getElementById(`req-${key}-met`).classList.toggle("hidden", !met);
  const text = document.getElementById(`req-${key}-text`);
  text.classList.toggle("text-gray-500", !met);
  text.classList.toggle("text-green-600", met);
}

// Checks new password meets strength rule (at least 8 chars, uppercase, number, symbol)
function validatePassword() {
  document.getElementById("password-general-error").classList.add("hidden");

  const password = document.getElementById("new_password1").value;

  const hasLength = password.length >= 8 && !/\s/.test(password);
  const hasUpper = /[A-Z]/.test(password);
  const hasNumber = /\d/.test(password);
  const hasSymbol = /[!@#$%^&*(),.?":{}|<>_\-+=~`\[\]\\/;']/.test(password);

  setRequirementMet("length", hasLength);
  setRequirementMet("upper", hasUpper);
  setRequirementMet("number", hasNumber);
  setRequirementMet("symbol", hasSymbol);
}

// Warns if the new password is the same as the current/old password
function validateSamePassword() {
  const oldPassword = document.getElementById("old_password").value;
  const newPassword1 = document.getElementById("new_password1").value;

  const bothFilled = oldPassword.length > 0 && newPassword1.length > 0;
  const isDifferent = bothFilled && oldPassword !== newPassword1;

  setRequirementMet("different", isDifferent);
}

// Runs all password checks on submit; opens the confirm modal only if everything passes
function handlePasswordFormSubmit(event) {
  const oldPassword = document.getElementById("old_password").value.trim();
  const newPassword1 = document.getElementById("new_password1").value.trim();
  const newPassword2 = document.getElementById("new_password2").value.trim();

  if (!oldPassword || !newPassword1 || !newPassword2) {
    return true;
  }

  const strengthRegex =
    /^(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*(),.?":{}|<>_\-+=~`\[\]\\/;'])[^\s]{8,}$/;

  if (!strengthRegex.test(newPassword1)) {
    event.preventDefault();
    validatePassword();
    document.getElementById("password-requirements").classList.remove("hidden");
    document
      .getElementById("password-general-error")
      .classList.remove("hidden");
    document.getElementById("new_password1").focus();
    return false;
  }

  if (newPassword1 !== newPassword2) {
    event.preventDefault();
    document
      .getElementById("password-match-validation")
      .classList.remove("hidden");
    return false;
  }

  if (oldPassword === newPassword1) {
    event.preventDefault();
    validateSamePassword();
    return false;
  }

  event.preventDefault();
  openPasswordConfirmModal();
  return false;
}

// Shows the "Are you sure?" modal before actually changing the password
function openPasswordConfirmModal() {
  const modal = document.getElementById("password_confirm_modal");
  modal.classList.remove("hidden");
  modal.classList.add("flex");
}

// Hides the confirm modal (Cancel button or click-outside)
function closePasswordConfirmModal() {
  const modal = document.getElementById("password_confirm_modal");
  if (modal.dataset.submitting === "true") return;
  modal.classList.add("hidden");
  modal.classList.remove("flex");
}

// User confirmed in the modal — actually submits the password form now
function submitPasswordForm() {
  const modal = document.getElementById("password_confirm_modal");
  if (modal.dataset.submitting === "true") return;
  modal.dataset.submitting = "true";
  document.getElementById("confirm_password_btn").setAttribute("aria-busy", "true");
  document.getElementById("cancel_password_btn").disabled = true;
  document.getElementById("change_password_btn").disabled = true;
  setButtonLoading(
    "confirm_password_btn",
    "confirm_password_spinner",
    "confirm_password_text",
    "Updating...",
  );
  document.getElementById("password_form").submit();
}

// Closes the modal if user clicks the dark overlay outside the box
function initPasswordModalOverlay() {
  const modal = document.getElementById("password_confirm_modal");
  if (!modal) return;
  modal.addEventListener("click", function (e) {
    if (e.target === modal) closePasswordConfirmModal();
  });
}

// Checks that "New Password" and "Confirm Password" fields match
function validatePasswordMatch() {
  const password1 = document.getElementById("new_password1");
  const password2 = document.getElementById("new_password2");
  const matchValidation = document.getElementById("password-match-validation");

  if (password1.value.length === 0 || password2.value.length === 0) {
    matchValidation.classList.add("hidden");
    return;
  }

  if (password1.value === password2.value) {
    matchValidation.classList.add("hidden");
  } else {
    matchValidation.classList.remove("hidden");
  }
}
