Feature: Optimizing a prompt of Qubit Notes
  As the developer of Qubit Notes
  I want to improve the prompts its agents are given, from what they were really asked
  So that each agent gets better, with a record of how its prompt was improved and why

  The experiments are kept apart from the app, in this repository: it never imports the app, never changes it, and
  only reads what it needs of it, from the app's database (QUBIT_NOTES_EXPERIMENT_APP_DATABASE_URL), with a
  connection that can only read. A prompt optimized here is given to the app in a step of its own, later; nothing here
  writes to the app.

  An experiment varies one prompt, e.g. "quiz", and nothing else: the samples it is run on, the model that runs it and
  the judge are the same for every version of it. It optimizes the prompt by Self-Supervised Prompt Optimization
  (SPO; Xiang et al., 2025), which needs no correct answers, only the prompt's requirements:

    1. The starting prompt is run on a few samples (3 by default), and is the best so far.
    2. In each round, the optimizer, a model, is given the best prompt so far, its outputs and the requirements, and
       writes a new version of the prompt.
    3. The new version is run on the same samples.
    4. The judge, a model, compares the new version's outputs with the best version's, sample by sample, several
       times over (4 by default), with the two in one order half the times and the other order the other half, so
       that it doesn't favour the first or the second. The version preferred in more than half of the verdicts is the
       best so far; on a tie, the best stays.
    5. After the last round (10 by default), the best version is the experiment's result.

  SPO judges on a few samples, so a version can win on them and do worse on others. The result is therefore checked
  once more, on held-out samples (10 by default): samples of the same prompt, none of them among those it was
  optimized on. The judge compares the result's outputs on them with the starting prompt's, in the same way, and the
  result is recommended for the app only if it is preferred in more than half of those verdicts.

  A prompt's requirements, experiments/<prompt>/requirements.md, say what a good output of it is, e.g. for "quiz":
  questions that test understanding, not wording, each answered by the section it cites. The optimizer and the judge
  are both given them. A new version must keep what the app relies on: the placeholders the app fills in, and the form
  of the output the app reads, e.g. a quiz's JSON. A version that doesn't loses its round, without being judged.

  The samples are what the app's agents were really given, rebuilt from its database:

    | prompt          | a sample is                                                                        |
    | study-assistant | a question a student asked, and the text of the sections its answer drew on       |
    | quiz            | the text of the section, material or topic a quiz was asked on, and how many questions |
    | summaries       | the text of the section, material or topic a summary was asked of                 |
    | flashcards      | the text of the section, material or topic a deck was asked of, and how many cards |

  They are drawn at random, with a seed, so an experiment can be run again on the same samples; or from the replies
  students rated down, to improve what they didn't like. They are only ever drawn from the material of the students
  named (the experimenter's own, by default), as the material is theirs.

  Every model is a real one: the optimizer, the prompt's and the judge's, each named with its provider, e.g.
  anthropic:claude-sonnet-5-5. These scenarios run with the cheapest models (QUBIT_NOTES_EXPERIMENT_TEST_MODEL, e.g.
  anthropic:claude-haiku-5-5), few samples and few rounds, so what the models answer differs from one run to the next:
  they check what the experiment does with the answers, never what the answers are.

  An experiment is tracked in Langfuse, in a project of its own, apart from the app's traces: its samples are a
  dataset, each round is a run of it, every call of the optimizer, the prompt and the judge is traced, and each version
  of the prompt is kept in Langfuse's prompt management. As Langfuse keeps traces for 30 days, the experiment is also
  kept here, in experiments/<prompt>/<experiment>/: each version of the prompt, its outputs, the judge's verdicts and
  reasons, the held-out check, and what each round cost.

  Background:
    Given the app's database has the material "fem-notes.md" of "maria", in "aeronautics/numerical-methods/finite-elements":
      """
      # 1 Introduction
      The finite element method divides a domain into small elements.

      # 2 Shape functions
      A linear element interpolates linearly between its two nodes.

      # 3 Assembly
      The element matrices are added into one global matrix.

      # 4 Boundary conditions
      Fixed values at the boundary are imposed on the global system.

      # 5 Solving
      The global system is solved for the values at the nodes.
      """
    And "maria" has asked for a quiz on each of its sections in the app, with the prompt "quiz@1"
    And the prompt "quiz" has its requirements

  Rule: An experiment reads the app, and never changes it

    Scenario: The app's database is only read
      When I optimize the prompt "quiz" for 1 round, with 2 samples and 2 held out, of "maria"
      Then the experiment read the app's database with a connection that can only read
      And nothing in the app's database changed

    Scenario: The starting prompt is the version the app uses
      When I optimize the prompt "quiz" for 1 round, with 2 samples and 2 held out, of "maria"
      Then the experiment started from "quiz@1", as the app's database records it, with its hash

    Scenario: A prompt the app hasn't used has no samples
      When I optimize the prompt "comprehension-check" for 1 round, with 2 samples and 2 held out, of "maria"
      Then I am told the app has no samples of "comprehension-check" for "maria"

    Scenario: A prompt without requirements can't be optimized
      Given the prompt "summaries" has no requirements
      When I optimize the prompt "summaries" for 1 round, with 2 samples and 2 held out, of "maria"
      Then I am told to write experiments/summaries/requirements.md first

  Rule: The samples are what the app's agents were really given

    Scenario: A quiz's sample is the text it was asked on
      When I draw 3 samples of the prompt "quiz", of "maria"
      Then each sample is the text of a section of "fem-notes.md" that a quiz was asked on, and its number of questions

    Scenario: The same seed draws the same samples
      Given I have drawn 3 samples of the prompt "quiz", of "maria", with the seed 7
      When I draw 3 samples of the prompt "quiz", of "maria", with the seed 7
      Then they are the same samples

    Scenario: Samples from the replies students rated down
      Given "maria" has asked the assistant 5 questions in the app, and rated the answers of 2 of them down
      When I draw 3 samples of the prompt "study-assistant", of "maria", from the replies rated down
      Then I am given the 2 samples rated down, and told there aren't 3

    Scenario: Only the material of the students named is drawn from
      Given "pedro" has asked for a quiz on his own material in the app
      When I draw 3 samples of the prompt "quiz", of "maria"
      Then no sample is of pedro's material

  Rule: Each round, the optimizer writes a new version, and the judge keeps the better

    Scenario: A round's new version is written from the best so far
      When I optimize the prompt "quiz" for 1 round, with 2 samples and 2 held out, of "maria"
      Then round 1 has a new version of "quiz", written by the optimizer from "quiz@1", its outputs and the requirements
      And the new version was run on the same 2 samples as "quiz@1"

    Scenario: The judge compares each sample in both orders
      When I optimize the prompt "quiz" for 1 round, with 2 samples and 2 held out, of "maria", the judge comparing each 4 times
      Then the judge compared each sample's two outputs 4 times, 2 with the new version's first and 2 with the best's first
      And each verdict is kept with the judge's reason

    Scenario: The best so far is the version the judge preferred more often
      When I optimize the prompt "quiz" for 2 rounds, with 2 samples and 2 held out, of "maria"
      Then after each round, the best so far is the new version if it was preferred in more than half of the round's verdicts, else the best before it

    Scenario: A version that drops a placeholder loses its round
      Given a version of "quiz" without the placeholder of the material's text
      When I run a round of "quiz" with that version, against "quiz@1", with 2 samples of "maria"
      Then the round is lost, as the version doesn't fill in the material's text
      And its outputs weren't judged

    Scenario: A version whose outputs the app couldn't read loses its round
      Given a version of "quiz" that asks for the quiz as a list in prose, not JSON
      When I run a round of "quiz" with that version, against "quiz@1", with 2 samples of "maria"
      Then the round is lost, as the app couldn't read its quizzes

    Scenario: The best version after the last round is the result
      When I optimize the prompt "quiz" for 3 rounds, with 2 samples and 2 held out, of "maria"
      Then the experiment has 3 rounds
      And its result is the version that was best after the last round, with the round it was written in

  Rule: The result is recommended only if it does better on samples it wasn't optimized on

    Scenario: The held-out samples are none of those the prompt was optimized on
      When I optimize the prompt "quiz" for 1 round, with 2 samples and 3 held out, of "maria"
      Then the held-out check was made on 3 samples of "quiz", none of them among the 2 it was optimized on

    Scenario: The result is compared with the starting prompt on the held-out samples
      When I optimize the prompt "quiz" for 2 rounds, with 2 samples and 3 held out, of "maria"
      Then if the result is a new version, the judge compared its outputs with those of "quiz@1" on each held-out sample, in both orders
      And the result is recommended if it was preferred in more than half of those verdicts, and not recommended otherwise

    Scenario: A result that is the starting prompt isn't checked
      Given an experiment of "quiz" whose result is "quiz@1", as no round found a better version
      When its result is checked on held-out samples
      Then no held-out check is made, and I am told the experiment found nothing better than "quiz@1"

    Scenario: Too few samples to hold any out
      When I optimize the prompt "quiz" for 1 round, with 3 samples and 3 held out, of "maria"
      Then I am told "maria" has 5 samples of "quiz", fewer than the 6 asked for, before anything is run

  Rule: An experiment is tracked in Langfuse, and kept here

    Scenario: The samples, the rounds and the versions are in Langfuse
      When I optimize the prompt "quiz" for 2 rounds, with 2 samples and 2 held out, of "maria"
      Then Langfuse's experiment project has the experiment's samples and held-out samples as datasets
      And a run of it for each round and for the held-out check, with the judge's verdicts as scores
      And each version of "quiz" in its prompt management, the result labelled "best"

    Scenario: The experiment is kept here too
      When I optimize the prompt "quiz" for 2 rounds, with 2 samples and 2 held out, of "maria"
      Then experiments/quiz/<experiment>/ has, for each round, the version, its outputs, the judge's verdicts and reasons, and what it cost
      And the held-out check, and whether the result is recommended
      And the experiment's samples, seed, models and result

    Scenario: The app's traces and the experiment's are kept apart
      When I optimize the prompt "quiz" for 1 round, with 2 samples and 2 held out, of "maria"
      Then nothing was traced in the app's Langfuse project

  Rule: An experiment stays within its budget

    Scenario: An experiment stops at its budget
      When I optimize the prompt "quiz" for 10 rounds, with 2 samples and 2 held out, of "maria", within a budget of $0.01
      Then the experiment stops before it costs more than $0.01
      And it is kept as stopped at its budget, with the rounds it ran and the best version so far, and no held-out check

    Scenario: What an experiment cost
      When I optimize the prompt "quiz" for 1 round, with 2 samples and 2 held out, of "maria"
      Then the experiment says what the optimizer, the prompt and the judge cost, in tokens and in dollars
