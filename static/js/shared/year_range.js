// static/js/shared/year_range.js
//
// Reads the From / To year <select> pair that a visualisation panel renders
// (ids `<prefix>-year-from` and `<prefix>-year-to`, produced by the
// year_range() macro in explore.html) and returns URLSearchParams ready to
// append to an API call.
//
// The server also tolerates blank / reversed / junk values, so this stays
// deliberately small: it only skips blanks and puts a reversed pair in order
// without touching what the user sees in the controls.

export function yearRangeQuery(prefix) {
  const val = id => {
    const el = document.getElementById(id);
    return el ? el.value : '';
  };
  let from = val(prefix + '-year-from');
  let to   = val(prefix + '-year-to');
  if (from && to && parseInt(from, 10) > parseInt(to, 10)) {
    [from, to] = [to, from];
  }
  const params = new URLSearchParams();
  if (from) params.set('year_from', from);
  if (to)   params.set('year_to',   to);
  return params;
}
