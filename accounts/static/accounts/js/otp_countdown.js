(() => {
  const countdown = document.querySelector('[data-otp-countdown]');
  if (!countdown) return;
  const time = countdown.querySelector('[data-otp-time]');
  const expired = document.querySelector('[data-otp-expired]');
  const remaining = Math.max(0, Number(countdown.dataset.otpCountdown) || 0);
  const started = Date.now();
  const monotonicStarted = performance.now();
  function update() {
    // Recompute elapsed time after background tabs/sleep instead of counting ticks.
    const elapsed = Math.max(Date.now() - started, performance.now() - monotonicStarted);
    const seconds = Math.max(0, Math.ceil((remaining - elapsed) / 1000));
    time.textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
    if (!seconds) {
      countdown.hidden = true;
      expired.hidden = false;
      expired.textContent = 'This code has expired. Select “Send a new code” to try again.';
      clearInterval(interval);
    }
  }
  const interval = setInterval(update, 1000);
  document.addEventListener('visibilitychange', update);
  window.addEventListener('pageshow', update);
  update();
})();
