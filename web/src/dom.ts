// Small DOM helpers shared by the side panel and the map popups.
//
// Both build the same kinds of thing — a labelled row, a clickable id chip — and
// building them as nodes rather than HTML strings means an OSM tag value can never
// be mistaken for markup.

export function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/**
 * An id the reviewer can click to put that feature on the map. Ids that name
 * nothing drawn stay visible but inert, so a dangling reference reads as a
 * dangling reference rather than a broken button.
 */
export function chip(label: string, onClick?: () => void): HTMLElement {
  if (!onClick) return element("span", "chip inert", label);
  const button = element("button", "chip", label);
  button.type = "button";
  button.addEventListener("click", onClick);
  return button;
}

/** A coloured status word — connector status or decision state. */
export function pill(status: string, label?: string): HTMLElement {
  return element("span", `pill ${status}`, label ?? status.replaceAll("_", " "));
}

export function definitionRow(list: HTMLElement, term: string, value: Node | string): void {
  list.append(element("dt", undefined, term));
  const definition = element("dd");
  if (typeof value === "string") definition.textContent = value;
  else definition.append(value);
  list.append(definition);
}
