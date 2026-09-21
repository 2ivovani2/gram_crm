# Gramly product design

Gramly helps a channel owner welcome a new Telegram participant. Every screen should make the next useful action obvious and keep the actual Telegram message visible.

## Visual system

- Ink `#070908` is the primary canvas, graphite `#111411` the surface, paper `#F0EEE4` the foreground and lime `#DDF45B` the product accent.
- Cyan `#55CFE8` is reserved for information and connection states. Mint means success and coral means an error or destructive action.
- Onest is the interface face and Manrope the compact display face. Use large headings, short measures, thin rules and generous empty space.
- Cubigator is the guide. Use the approved mascot asset at useful sizes; do not add text inside mascot artwork.
- Real Telegram messages and controls explain the product better than abstract diagrams.

## Components and motion

- Mini App uses Lumen React primitives for buttons, forms, cards, status, feedback and overlays. Business components such as the chain editor and Telegram preview remain Gramly components.
- Public marketing pages use the same tokens but custom markup. Lumen must not turn the landing into a generic component gallery.
- Motion explains state changes. The landing's signature is a lime signal travelling from a join request to a personalised delivered message.
- Respect `prefers-reduced-motion`; no interaction depends on animation.

## Accessibility

- Interactive controls have visible focus, a 44 px minimum touch target and a textual accessible name.
- Status is expressed by text as well as colour. Dialogs trap focus and return it to their trigger.
- Layouts must work at 390, 430 and 1440 px and with long Russian copy.
