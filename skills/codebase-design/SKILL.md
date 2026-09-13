---
name: codebase-design
description: Shared vocabulary for designing deep modules. Use when the user wants to design or improve a module's interface, find deepening opportunities, decide where a seam goes, make code more testable or AI-navigable, or when another skill needs the deep-module vocabulary.
---

# Codebase Design

Design **deep modules**: a lot of behavior behind a small interface, placed at a clean seam, testable through that interface. Use this language and these principles wherever code is being designed or restructured. The aim is leverage for callers, locality for maintainers, and testability for everyone.

## Glossary

Use these terms exactly. Do not substitute "component," "service," "API," or "boundary." Consistent language is the point.

**Module** - anything with an interface and an implementation. Deliberately scale-agnostic: a function, class, package, or tier-spanning slice. Avoid: unit, component, service.

**Interface** - everything a caller must know to use the module correctly: the type signature, but also invariants, ordering constraints, error modes, required configuration, and performance characteristics. Avoid: API, signature.

**Implementation** - what is inside a module, its body of code. Distinct from **Adapter**: a thing can be a small adapter with a large implementation, such as a Postgres repository, or a large adapter with a small implementation, such as a protocol-compatible local runtime. Reach for "adapter" when the seam is the topic; "implementation" otherwise.

**Depth** - leverage at the interface: the amount of behavior a caller or test can exercise per unit of interface they have to learn. A module is **deep** when a large amount of behavior sits behind a small interface, and **shallow** when the interface is nearly as complex as the implementation.

**Seam** - a place where you can alter behavior without editing in that place; the location at which a module's interface lives. Where to put the seam is its own design decision, distinct from what goes behind it. Avoid: boundary.

**Adapter** - a concrete thing that satisfies an interface at a seam. Describes role, not substance.

**Leverage** - what callers get from depth: more capability per unit of interface they learn. One implementation pays back across many call sites and tests.

**Locality** - what maintainers get from depth: change, bugs, knowledge, and verification concentrate in one place rather than spreading across callers. Fix once, fixed everywhere.

## Deep vs Shallow

**Deep module** = small interface plus lots of implementation:

```text
+-------------------+
|  Small Interface  |
+-------------------+
|                   |
| Deep              |
| Implementation    |
|                   |
+-------------------+
```

**Shallow module** = large interface plus little implementation:

```text
+-------------------------+
|     Large Interface     |
+-------------------------+
|   Thin Implementation   |
+-------------------------+
```

When designing an interface, ask:

- Can I reduce the number of methods?
- Can I simplify the parameters?
- Can I hide more complexity inside?

## Principles

- **Depth is a property of the interface, not the implementation.** A deep module can be internally composed of small, swappable parts exercised through the real Interface; those parts are not additional public Interfaces. Test convenience never creates a second production Seam.
- **The deletion test.** Imagine deleting the module. If complexity vanishes, it was a pass-through. If complexity reappears across callers, it was earning its keep.
- **The interface is the test surface.** Callers and tests cross the same seam. If you want to test past the interface, the module is probably the wrong shape.
- **One runtime adapter means a hypothetical seam. Two genuine runtime variants can justify a real one.** A test-only substitute does not count.

## Designing for Testability

Good interfaces make testing natural:

1. **Accept dependencies, do not create them.**

   ```typescript
   // Testable
   function processOrder(order, paymentGateway) {}

   // Hard to test
   function processOrder(order) {
     const gateway = new StripeGateway();
   }
   ```

2. **Return results, do not produce side effects.**

   ```typescript
   // Testable
   function calculateDiscount(cart): Discount {}

   // Hard to test
   function applyDiscount(cart): void {
     cart.total -= discount;
   }
   ```

3. **Small surface area.** Fewer methods means fewer tests. Fewer params means simpler setup.

## Relationships

- A **Module** has exactly one **Interface**: the surface it presents to callers and tests.
- **Depth** is a property of a **Module**, measured against its **Interface**.
- A **Seam** is where a **Module**'s **Interface** lives.
- An **Adapter** sits at a **Seam** and satisfies the **Interface**.
- **Depth** produces **Leverage** for callers and **Locality** for maintainers.

## Rejected Framings

- **Depth as ratio of implementation-lines to interface-lines:** rewards padding the implementation. Use depth-as-leverage instead.
- **"Interface" as the TypeScript `interface` keyword or a class's public methods:** too narrow. Interface here includes every fact a caller must know.
- **"Boundary":** overloaded. Say **seam** or **interface**.

## Going Deeper

- **Deepening a cluster given its dependencies** - see [DEEPENING.md](DEEPENING.md): dependency categories, seam discipline, and replace-don't-layer testing.
- **Exploring alternative interfaces** - see [DESIGN-IT-TWICE.md](DESIGN-IT-TWICE.md): design the interface several radically different ways, then compare on depth, locality, and seam placement.
