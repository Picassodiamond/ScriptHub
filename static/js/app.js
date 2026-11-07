(function () {
  // Dismissible flash messages
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-dismiss]');
    if (b) b.closest('.flash').remove();
  });
  // Success messages fade away on their own
  [].forEach.call(document.querySelectorAll('.flash[data-autohide]'), function (el) {
    setTimeout(function () {
      el.classList.add('flash-out');
      setTimeout(function () { el.remove(); }, 400);
    }, 5000);
  });
  // Confirm before submitting forms that carry data-confirm
  document.addEventListener('submit', function (e) {
    var msg = e.target.getAttribute('data-confirm');
    if (msg && !window.confirm(msg)) { e.preventDefault(); return; }
    // Double-click guard: buttons marked data-once are disabled once submitted
    [].forEach.call(e.target.querySelectorAll('[data-once]'), function (b) {
      setTimeout(function () { b.disabled = true; }, 0);
    });
  });
})();
