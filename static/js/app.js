(function () {
  // Dismissible flash messages
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-dismiss]');
    if (b) b.closest('.flash').remove();
  });
  // Confirm before submitting forms that carry data-confirm
  document.addEventListener('submit', function (e) {
    var msg = e.target.getAttribute('data-confirm');
    if (msg && !window.confirm(msg)) e.preventDefault();
  });
})();
