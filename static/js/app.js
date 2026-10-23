// Progressive enhancement only: every page works without this file.
(function () {
  // Transaction form: show the destination account only for transfers.
  var kindInputs = document.querySelectorAll('input[name="kind"]');
  var toRow = document.getElementById('row-to_account');
  var catRow = document.getElementById('row-category');
  function syncKind() {
    var checked = document.querySelector('input[name="kind"]:checked');
    if (!checked || !toRow) return;
    var isTransfer = checked.value === 'transfer';
    toRow.hidden = !isTransfer;
    if (catRow) catRow.hidden = isTransfer;
  }
  kindInputs.forEach(function (el) { el.addEventListener('change', syncKind); });
  syncKind();

  // Select-all checkbox on the transaction list.
  var all = document.getElementById('select-all');
  if (all) {
    all.addEventListener('change', function () {
      document.querySelectorAll('input[name="ids"]').forEach(function (box) { box.checked = all.checked; });
    });
  }
})();
