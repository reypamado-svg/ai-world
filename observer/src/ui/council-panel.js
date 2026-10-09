// Council news (O5): in a civilization's perspective, the world chronicle is hidden (its
// events can carry facts that civilization could not know) and this panel lists its council's
// own dated records instead: battles it fought, notices it received, spies it caught, what its
// spies brought home, messages delivered to it, its treaties and what struck it. Go moves the
// camera to a tile the item names.

import { describeNews } from '../data/perspective-source.js';
import { civilizationLabel } from '../data/naming.js';

export class CouncilPanel {
  /**
   * @param {HTMLElement} root the panel
   * @param {{ onGo: (q: number, r: number) => void }} hooks
   */
  constructor(root, hooks) {
    this.root = root;
    this.list = root.querySelector('#council-list');
    this.title = root.querySelector('#council-day');
    this.hooks = hooks;
    this.perspective = null;
  }

  setPerspective(perspective) {
    this.perspective = perspective;
    this.render();
  }

  render() {
    const p = this.perspective;
    this.list.replaceChildren();
    if (!p) return;
    this.title.textContent = `${civilizationLabel(p.civilization)} · its council's report for day ${p.day}`;
    if (!p.news.length) {
      const li = document.createElement('li');
      li.className = 'empty';
      li.textContent = 'Nothing recorded for its council yet.';
      this.list.append(li);
      return;
    }
    for (const item of p.news) {
      const li = document.createElement('li');
      li.dataset.kind = item.kind;
      const what = document.createElement('div');
      what.className = 'what';
      what.textContent = describeNews(item);
      const when = document.createElement('div');
      when.className = 'where';
      when.textContent = `day ${item.day}${item.tile ? ` · tile ${item.tile[0]},${item.tile[1]}` : ''}`;
      li.append(what, when);
      if (item.tile) {
        const actions = document.createElement('div');
        actions.className = 'actions';
        const go = document.createElement('button');
        go.className = 'go';
        go.textContent = 'Go';
        go.addEventListener('click', () => this.hooks.onGo(item.tile[0], item.tile[1]));
        actions.append(go);
        li.append(actions);
      }
      this.list.append(li);
    }
  }
}
