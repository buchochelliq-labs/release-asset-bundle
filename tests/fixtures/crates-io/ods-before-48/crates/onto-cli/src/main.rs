const SKILL: &str = include_str!("../../../skills/onto.md");

fn main() {
    println!("{} {SKILL}", onto_core::NAME);
}
