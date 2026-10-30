# Remymy (a.k.a Remy2): An Agentic Conversational Meal-Ranking System

## EXECUTIVE SUMMARY / PAPER ABSTRACT

Remy is an agentic chatbot weekly meal recommendation application that helps users choose recipes based on their preferences. The project treats meal planning as a data-integration and reasoning problem. A recipe is represented through its ingredient requirements; each requirement is linked to a normalized food concept; candidate nutrition profiles are used to estimate nutrients and FairPrice evidence is attached to the product or offer that can satisfy the requirement. A generic food profile, a branded product, and a dated retail offer therefore remain separate records.

A typical user request may combine a carbohydrate range, an excluded allergen, a halal or vegetarian preference, and a daily food budget. Remy models these as explicit constraints. Its reasoning flow is to identify the ingredient occurrences required by a recipe, find compatible food candidates, verify nutrition and dietary evidence, calculate the portion-level contribution, and estimate cost when package and price information are sufficient.

---

## CREDITS / PROJECT CONTRIBUTION

| Official Full Name    | Student ID (MTech Applicable) | Work Items (Who Did What)                                | Email (Optional)        |
| :-------------------- | :---------------------------: | :------------------------------------------------------- | :---------------------- |
| Le Chi Thanh          |           A0352811L           | Front-end, data preparation, MCP integration and backend | thanhlc@u.nus.edu       |
| Vuong Quang Viet Tung |           A0357416Y           | Machine learning model and for model serving             | viettungvuong@u.nus.edu |
| Tran Dinh Gia Khanh   |           A0359890J           | Evaluation service, backend and data preparation         | khanh.dg.tran@u.nus.edu |

---

## VIDEO OF SYSTEM MODELLING & USE CASE DEMO

You can find [here](https://drive.google.com/file/d/11lFl4Xv3IZC8FCapV9nnhjy8qy8ci4rh/view?usp=drive_link) for our final presentation and demo for our application.

---

## USER GUIDE

Read the [PDF user guide](Reports/Remymy_User_Guide.pdf) for step-by-step Docker startup, data seeding, browser workflows, and evaluation checks.

1. Start Docker Desktop and confirm `docker info` succeeds.
2. Prepare the data and model files described in the guide; these large assets are not included in a fresh checkout.
3. From the repository root, start the full stack:

   ```bash
   cd SystemCode/remymy
   docker compose --env-file .env.nonprod --profile recommender \
     up --build -d --wait --wait-timeout 900
   ```

   Create `.env.nonprod` using the guide's settings if it does not exist. For recipe browsing without the MCP/model services, omit `--profile recommender`.

4. Open [Remymy](http://localhost:3000), set **Preferences**, browse **Meals**, rate recipes, and use **Saved**, **History**, and **My recipes**. Connect a provider under **AI connection** to use **Discover** chat.
5. Open [Langfuse](http://localhost:3030) to inspect traces. Follow the guide's evaluation steps to capture ranking snapshots, run the Jev judge with your own `JEV_API_KEY`, export reports, and check scores. Traces alone do not indicate that an evaluation has run.

For service configuration details, see the [stack README](SystemCode/remymy/README.md).

---

## MISCELLANEOUS

`Refer to Github Folder: Miscellaneous`

Contains all of the initialize prompts for each of the component we made in this project, please check
---
